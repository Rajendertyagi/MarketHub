"""F&O coverage service — routing + coverage for the canonical F&O universe.

Answers: *"which F&O contracts should be streamed, which provider receives
each contract, and what is the current coverage state?"*

It does NOT reconcile feeds — that stays in ``SubscriptionService``. This
service only computes the desired, provider-routed plan (and its coverage),
using:

  * the canonical F&O universe (market.derivatives_universe, catalog-driven)
  * the persisted F&O configuration (core.persistence fno_config)
  * the provider registry (capabilities / capacity / budgets / preference)
  * live provider runtime status (SourceManager, injected)

Broker-neutral: no broker is hardcoded; providers come from the registry and
each canonical contract maps to at most one provider by default.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from market.coverage_states import (
    CAPACITY_LIMITED,
    LIVE,
    PROVIDER_UNAVAILABLE,
    STALE,
    UNCOVERED,
    UNSUBSCRIBED,
)
from market.derivatives_universe import (
    FUTURE,
    KIND_EQUITY,
    KIND_INDEX,
    fno_underlyings,
    futures_index,
    index_underlyings,
)
from providers.capabilities import CAP_LTP, CAP_OI

logger = logging.getLogger(__name__)

# Coverage states live in the canonical market layer (market.coverage_states)
# and are re-exported here for existing consumers.

# Capabilities required to stream an F&O contract (LTP + open interest).
REQUIRED_FNO_CAPS: frozenset[str] = frozenset({CAP_LTP, CAP_OI})

# Provider runtime states that mean "can receive subscriptions now".
# A provider that is merely CONNECTING or RECONNECTING is not delivering
# data: a contract is only LIVE when the provider is actually streaming.
_AVAILABLE_STATES = frozenset({"streaming"})


@dataclass
class RoutedContract:
    underlying: str
    kind: str
    exchange: str
    expiry: str
    instrument_type: str
    provider: str | None = None
    provider_token: str | None = None
    tradingsymbol: str | None = None
    lot_size: int | None = None
    state: str = UNCOVERED
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "underlying": self.underlying,
            "kind": self.kind,
            "exchange": self.exchange,
            "expiry": self.expiry,
            "instrument_type": self.instrument_type,
            "provider": self.provider,
            "provider_token": self.provider_token,
            "tradingsymbol": self.tradingsymbol,
            "lot_size": self.lot_size,
            "state": self.state,
            "reason": self.reason,
        }


@dataclass
class RoutingPlan:
    enabled: bool
    expiry_requested: str
    expiry_resolved: str | None
    expiries: list[str] = field(default_factory=list)
    desired: int = 0
    routed: int = 0
    by_provider: dict[str, int] = field(default_factory=dict)
    keys_by_provider: dict[str, list[str]] = field(default_factory=dict)
    states: dict[str, int] = field(default_factory=dict)
    capacity: dict[str, dict[str, Any]] = field(default_factory=dict)
    providers: list[dict[str, Any]] = field(default_factory=list)
    uncovered: list[RoutedContract] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    contracts: list[RoutedContract] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "expiry_requested": self.expiry_requested,
            "expiry_resolved": self.expiry_resolved,
            "expiries": list(self.expiries),
            "desired": self.desired,
            "routed": self.routed,
            "by_provider": dict(self.by_provider),
            "states": dict(self.states),
            "capacity": {k: dict(v) for k, v in self.capacity.items()},
            "providers": [dict(p) for p in self.providers],
            "uncovered": [c.to_dict() for c in self.uncovered],
            "errors": list(self.errors),
            "contracts": [c.to_dict() for c in self.contracts],
        }


@dataclass(frozen=True)
class _CatalogSkeleton:
    """Immutable/catalog-derived plan inputs (no dynamic provider state)."""

    catalog_fingerprint: tuple[Any, ...]
    config_key: tuple[Any, ...]
    symbols: tuple[str, ...]
    index_set: frozenset[str]
    expiries: tuple[str, ...]
    selected: dict[tuple[str, str], tuple[str, ...]]
    groups: dict[tuple[str, str, str, str], dict[str, dict[str, Any]]]
    equity_spot_map: dict[str, dict[str, str]]
    index_spot_keys: dict[str, dict[str, str]]
    errors: tuple[str, ...] = ()


class FnoCoverageService:
    """Broker-neutral F&O routing + coverage over the catalog + registry."""

    #: Documented defaults when no persisted configuration is available.
    _DEFAULT_CONFIG: dict[str, Any] = {
        "enabled": True, "index_labels": [], "stock_mode": "all",
        "stock_symbols": [], "preferred_provider": None,
        "fallback_provider": None, "expiry_policy": "nearest",
        "selected_expiry": None,
    }

    def __init__(
        self,
        catalog: Any,
        *,
        registry: Any = None,
        status_provider: Callable[[str], str | None] | None = None,
        subscribed_provider: Callable[[str], Iterable[str]] | None = None,
        config_reader: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._catalog = catalog
        self._registry = registry
        self._status_provider = status_provider
        self._subscribed_provider = subscribed_provider
        self._config_reader = config_reader
        self._skeleton_cache: dict[tuple[Any, ...], _CatalogSkeleton] = {}
        self._skeleton_cache_lock = threading.Lock()
        self._skeleton_build_locks: dict[tuple[Any, ...], threading.Lock] = {}
        self._skeleton_build_locks_guard = threading.Lock()

    # -- registry / config ---------------------------------------------------

    def _reg(self) -> Any:
        if self._registry is None:
            from providers.registry import get_registry
            self._registry = get_registry()
        return self._registry

    def _config(self) -> dict[str, Any]:
        """The persisted, validated F&O configuration.

        The durable ``fno_config`` row is the authority for the user's
        declared intent (provider preference/fallback, expiry policy, index
        selection, stock mode). When no reader is injected the documented
        defaults apply.
        """
        if self._config_reader is not None:
            try:
                stored = self._config_reader() or {}
            except Exception:
                stored = {}
            merged = dict(self._DEFAULT_CONFIG)
            for key, value in stored.items():
                if value is not None:
                    merged[key] = value
            return merged
        return dict(self._DEFAULT_CONFIG)

    def invalidate_plan_cache(self) -> None:
        """Drop cached catalog-derived plan skeletons.

        Call after catalog sync/expiry-affecting catalog changes. Dynamic
        provider state is always computed fresh per plan() call.
        """
        with self._skeleton_cache_lock:
            self._skeleton_cache.clear()

    def _catalog_fingerprint(self) -> tuple[Any, ...]:
        """Best-effort catalog sync fingerprint for skeleton invalidation.

        Returns a stable fingerprint only when catalog sync state is available
        and parseable. Unreliable/missing fingerprints must not be treated as
        proof that catalog-derived data is unchanged.
        """
        try:
            state = self._catalog.sync_state()
        except Exception:
            return ("unreliable-sync-state", "exception")
        if not state:
            return ("unreliable-sync-state", "empty")
        try:
            items = []
            for row in state:
                if isinstance(row, dict):
                    items.append(tuple(sorted(
                        (str(k), str(v)) for k, v in row.items())))
                else:
                    items.append(str(row))
            fingerprint = tuple(sorted(items))
            if not fingerprint:
                return ("unreliable-sync-state", "empty-items")
            return ("sync-state", fingerprint)
        except Exception:
            return ("unreliable-sync-state", "unparseable")

    @staticmethod
    def _fingerprint_cacheable(fingerprint: tuple[Any, ...]) -> bool:
        return bool(fingerprint) and fingerprint[0] == "sync-state"

    @staticmethod
    def _config_key(cfg: dict[str, Any]) -> tuple[Any, ...]:
        index_labels = tuple(sorted(str(x).strip().upper()
                                    for x in (cfg.get("index_labels") or [])
                                    if str(x).strip()))
        stock_symbols = tuple(sorted(str(x).strip().upper()
                                     for x in (cfg.get("stock_symbols") or [])
                                     if str(x).strip()))
        return (
            bool(cfg.get("enabled", True)),
            index_labels,
            str(cfg.get("stock_mode", "all")),
            stock_symbols,
            cfg.get("preferred_provider") or None,
            cfg.get("fallback_provider") or None,
            str(cfg.get("expiry_policy", "nearest")),
            cfg.get("selected_expiry") or None,
        )

    def _enabled_provider_key(self) -> tuple[str, ...]:
        """Enabled provider set affects universe symbol selection."""
        try:
            return tuple(sorted(str(p) for p in self._reg().enabled_ids()))
        except Exception:
            return ()

    def _skeleton_key(self, cfg: dict[str, Any], requested: str | None) -> tuple[Any, ...]:
        # Expiry selection depends on today's date (nearest/valid-expiry logic),
        # and universe selection depends on enabled providers. Include both so a
        # long-running process cannot reuse a stale skeleton across rollover or
        # provider enable/disable changes.
        return (
            requested or None,
            self._config_key(cfg),
            self._enabled_provider_key(),
            date.today().isoformat(),
            self._catalog_fingerprint(),
        )

    def _skeleton_build_lock(self, key: tuple[Any, ...]) -> threading.Lock:
        with self._skeleton_build_locks_guard:
            lock = self._skeleton_build_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._skeleton_build_locks[key] = lock
            return lock

    def _build_skeleton(self, cfg: dict[str, Any], requested: str | None,
                        catalog_fingerprint: tuple[Any, ...]) -> _CatalogSkeleton:
        config_key = self._config_key(cfg)
        symbols, index_set = self._universe_symbols(cfg)
        wanted = set(symbols)

        try:
            all_rows = []
            for exch in ("NSE", "BSE"):
                try:
                    all_rows.extend(
                        row
                        for rows in futures_index(
                            self._catalog, exchange=exch).values()
                        for row in rows
                    )
                except Exception:
                    pass
        except Exception as exc:
            return _CatalogSkeleton(
                catalog_fingerprint=catalog_fingerprint,
                config_key=config_key,
                symbols=tuple(symbols),
                index_set=frozenset(index_set),
                expiries=(),
                selected={},
                groups={},
                equity_spot_map={},
                index_spot_keys={},
                errors=(f"catalog future read failed: {type(exc).__name__}",),
            )

        expiries_by_contract: dict[tuple[str, str], set[str]] = {}
        for row in all_rows or []:
            und = str(row.get("underlying") or "").upper()
            if und not in wanted:
                continue
            exp = str(row.get("expiry") or "")
            if not exp:
                continue
            exch = str(row.get("exchange") or "NSE")
            expiries_by_contract.setdefault((exch, und), set()).add(exp)
        expiries = sorted({e for s in expiries_by_contract.values() for e in s})

        selected: dict[tuple[str, str], tuple[str, ...]] = {}
        for (exch, und), und_expiries in expiries_by_contract.items():
            chosen = self._nearest_expiries(und_expiries, cfg, requested, 2)
            if chosen:
                selected[(exch, und)] = tuple(chosen)

        errors: list[str] = []
        if not selected:
            errors.append("no listed futures expiry for the configured universe")

        groups: dict[tuple[str, str, str, str], dict[str, dict[str, Any]]] = {}
        for row in all_rows or []:
            und = str(row.get("underlying") or "").upper()
            if und not in wanted:
                continue
            exch = str(row.get("exchange") or "NSE")
            exp = str(row.get("expiry") or "")
            if exp not in selected.get((exch, und), ()):
                continue
            key = (exch, und, exp, FUTURE)
            groups.setdefault(key, {})[str(row.get("provider") or "")] = row

        equity_spot_map = self._equity_spot_map()
        index_spot_keys = self._index_spot_keys()

        return _CatalogSkeleton(
            catalog_fingerprint=catalog_fingerprint,
            config_key=config_key,
            symbols=tuple(symbols),
            index_set=frozenset(index_set),
            expiries=tuple(expiries),
            selected=selected,
            groups=groups,
            equity_spot_map=equity_spot_map,
            index_spot_keys=index_spot_keys,
            errors=tuple(errors),
        )

    _MAX_SKELETON_CACHE = 32

    def _prune_skeleton_state_locked(self) -> None:
        """Bound cache growth (caller holds relevant locks as needed)."""
        if len(self._skeleton_cache) > self._MAX_SKELETON_CACHE:
            # Drop oldest insertion-order entries (dict preserves insertion order).
            overflow = len(self._skeleton_cache) - self._MAX_SKELETON_CACHE
            for old_key in list(self._skeleton_cache.keys())[:overflow]:
                self._skeleton_cache.pop(old_key, None)
        # Keep build locks: pruning while a build is in-flight (key not yet
        # cached) can break single-flight by replacing the active lock object.

    def _get_skeleton(self, cfg: dict[str, Any], requested: str | None) -> _CatalogSkeleton:
        key = self._skeleton_key(cfg, requested)
        fingerprint = key[4]
        cacheable = self._fingerprint_cacheable(fingerprint)
        if cacheable:
            with self._skeleton_cache_lock:
                cached = self._skeleton_cache.get(key)
                if cached is not None:
                    return cached
        lock = self._skeleton_build_lock(key)
        with lock:
            if cacheable:
                with self._skeleton_cache_lock:
                    cached = self._skeleton_cache.get(key)
                    if cached is not None:
                        return cached
            skeleton = self._build_skeleton(cfg, requested, fingerprint)
            if cacheable:
                with self._skeleton_cache_lock:
                    self._skeleton_cache[key] = skeleton
                    self._prune_skeleton_state_locked()
            return skeleton

    # -- universe ------------------------------------------------------------

    def _universe_symbols(self, config: dict[str, Any]) -> tuple[list[str], set[str]]:
        """Return (all symbols in order, index-symbol set) for the config."""
        reg = self._reg()
        selected = [str(x).strip().upper()
                    for x in (config.get("index_labels") or []) if str(x).strip()]
        index_us = index_underlyings()
        if selected:
            index_us = [u for u in index_us if u.symbol in set(selected)]

        stocks: list[Any] = []
        mode = config.get("stock_mode", "all")
        if mode == "selected":
            wanted = {str(x).strip().upper()
                      for x in (config.get("stock_symbols") or [])}
            catalog = self._catalog
            for pid in reg.enabled_ids():
                for u in fno_underlyings(catalog, provider=pid):
                    if u.kind == KIND_EQUITY and u.symbol in wanted:
                        stocks.append(u)
        else:
            seen: set[str] = set()
            for pid in reg.enabled_ids():
                for u in fno_underlyings(self._catalog, provider=pid):
                    if u.kind == KIND_EQUITY and u.symbol not in seen:
                        seen.add(u.symbol)
                        stocks.append(u)

        symbols = [u.symbol for u in index_us] + [u.symbol for u in stocks]
        index_set = {u.symbol for u in index_us}
        return symbols, index_set

    # -- expiry --------------------------------------------------------------

    def _choose_expiry(self, expiries: Sequence[str], config: dict[str, Any],
                       requested: str | None) -> str | None:
        """Pick the resolved expiry from catalog-derived expiries.

        The expiry is resolved PER UNDERLYING (and per exchange) by the
        caller; this helper only applies the policy to one underlying's
        available expiries.
        """
        today = date.today().isoformat()
        available = sorted({str(e) for e in expiries if e and str(e) >= today})
        if requested:
            return requested if requested in available else None
        if config.get("expiry_policy", "nearest") == "selected":
            sel = config.get("selected_expiry")
            if sel and sel in available:
                return sel
        return available[0] if available else None

    def _nearest_expiries(self, expiries: Sequence[str], config: dict[str, Any],
                           requested: str | None, count: int) -> list[str]:
        """The nearest ``count`` valid expiries for ONE underlying.

        The nearest expiry is policy-selected (nearest / explicitly
        selected); the remaining expiries are the next ones up, so the
        streamed set is always the nearest two VALID expiries for that
        underlying.
        """
        today = date.today().isoformat()
        available = sorted({str(e) for e in expiries if e and str(e) >= today})
        if not available:
            return []
        if requested:
            if requested in available:
                chosen = [requested]
            else:
                return []
        elif config.get("expiry_policy", "nearest") == "selected":
            sel = config.get("selected_expiry")
            chosen = [sel] if sel and sel in available else [available[0]]
        else:
            chosen = [available[0]]
        for exp in available:
            if len(chosen) >= count:
                break
            if exp not in chosen:
                chosen.append(exp)
        return chosen[:count]

    # -- provider status -----------------------------------------------------
    def _status(self, provider: str) -> str | None:
        if self._status_provider is None:
            return None
        try:
            return self._status_provider(provider)
        except Exception:
            return None

    def _available(self, provider: str) -> bool:
        state = self._status(provider)
        if state is None:
            # No runtime info injected (e.g. tests) -> treat as available.
            return self._status_provider is None
        return state in _AVAILABLE_STATES

    def _subscribed(self, provider: str) -> set[str]:
        if self._subscribed_provider is None:
            return set()
        try:
            return set(self._subscribed_provider(provider) or ())
        except Exception:
            return set()

    # -- spot keys -----------------------------------------------------------

    def _index_spot_keys(self) -> dict[str, dict[str, str]]:
        """Canonical index label -> {provider: provider spot feed key}.

        Derived from the CATALOGUE (each provider's own feed key for the
        index) with the static registry as a fallback — the catalogue is
        the authority on the provider's identifier.
        """
        out: dict[str, dict[str, str]] = {}
        try:
            from app.market_indices import (
                MAJOR_INDICES,
                index_provider_keys,
            )
        except Exception:
            return out
        for e in MAJOR_INDICES:
            keys = index_provider_keys(self._catalog, e["label"])
            if not keys:
                keys = {"upstox": e["upstox_key"], "fyers": e["fyers_symbol"]}
            out[e["label"]] = keys
        return out

    def _equity_spot_map(self) -> dict[str, dict[str, str]]:
        """Bulk map: stock symbol -> {provider: equity feed key}.

        ONE query per provider (``equity_universe``) — never per underlying.
        The key is the provider's FEED key (the key its stream subscribes
        with and its quotes are stored under), resolved from the catalogue
        row.
        """
        out: dict[str, dict[str, str]] = {}
        for pid in self._reg().enabled_ids():
            try:
                rows = self._catalog.equity_universe(
                    provider=pid, limit=20000) or []
            except Exception:
                rows = []
            for r in rows:
                sym = str(r.get("tradingsymbol") or "").upper()
                if not sym:
                    continue
                key = self._feed_key_for(r, pid)
                if key:
                    out.setdefault(sym, {})[pid] = key
        return out

    def _spot_candidates(
        self, symbol: str, kind: str,
        equity_map: dict[str, dict[str, str]] | None = None,
        index_spot_keys: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, str]:
        if kind == KIND_INDEX:
            if index_spot_keys is not None:
                return index_spot_keys.get(symbol, {})
            return self._index_spot_keys().get(symbol, {})
        return (equity_map or {}).get(symbol, {})

    # -- routing -------------------------------------------------------------

    def plan(self, *, expiry: str | None = None,
             config: dict[str, Any] | None = None,
             required_caps: frozenset[str] = REQUIRED_FNO_CAPS) -> RoutingPlan:
        """Compute the desired provider-routed F&O plan (no feed changes).

        Catalog-derived computation (universe, futures rows/groups/expiries,
        spot-key maps) is cached and reused across calls when the catalog
        fingerprint and relevant config/expiry inputs are unchanged. Dynamic
        provider state (status/availability, subscribed keys, capacity usage,
        routing decisions) is always computed fresh per call.
        """
        cfg = config if config is not None else self._config()
        reg = self._reg()
        plan = RoutingPlan(enabled=bool(cfg.get("enabled", True)),
                           expiry_requested=expiry or "latest",
                           expiry_resolved=None)

        # Provider summaries (broker-neutral, dynamic).
        for pid in reg.ids():
            desc = reg.get(pid)
            plan.providers.append({
                "provider": pid,
                "label": getattr(desc, "label", pid),
                "enabled": reg.is_enabled(pid),
                "status": self._status(pid),
                "available": self._available(pid),
                "capabilities": sorted(getattr(desc, "capabilities", ())),
                "budget": reg.budget(pid),
            })
            plan.capacity[pid] = {
                "budget": reg.budget(pid), "used": 0, "available": reg.budget(pid)}

        if not plan.enabled:
            return plan

        skeleton = self._get_skeleton(cfg, expiry)
        plan.expiries = list(skeleton.expiries)
        if skeleton.errors:
            plan.errors.extend(skeleton.errors)
            if not skeleton.selected:
                plan.expiry_resolved = None
                return plan

        if not skeleton.selected:
            plan.errors.append(
                "no listed futures expiry for the configured universe")
            plan.expiry_resolved = None
            return plan
        plan.expiry_resolved = min(
            (exps[0] for exps in skeleton.selected.values() if exps), default=None)

        preferred = cfg.get("preferred_provider") or None
        fallback = cfg.get("fallback_provider") or None
        order = self._provider_order(preferred, fallback)

        contracts: list[RoutedContract] = []
        states: dict[str, int] = {}
        used: dict[str, int] = {pid: 0 for pid in reg.ids()}

        # SPOTS first: the underlying's own quote is the highest-priority
        # subscription (a spotless underlying is useless), so a saturated
        # provider budget can never starve it. Futures follow.
        for und in skeleton.symbols:
            kind = KIND_INDEX if und in skeleton.index_set else KIND_EQUITY
            candidates = self._spot_candidates(
                und, kind, skeleton.equity_spot_map, skeleton.index_spot_keys)
            if not candidates:
                continue
            contract, pid = self._route_spot(
                und, kind, candidates, order, required_caps, used)
            contracts.append(contract)
            states[contract.state] = states.get(contract.state, 0) + 1
            if pid is not None:
                used[pid] = used.get(pid, 0) + 1
                plan.keys_by_provider.setdefault(pid, []).append(
                    contract.provider_token or "")

        for (exchange, und, exp, itype), by_prov in sorted(skeleton.groups.items()):
            kind = KIND_INDEX if und in skeleton.index_set else KIND_EQUITY
            contract, pid = self._route_one(
                und, kind, exchange, exp, itype, by_prov, order,
                required_caps, used, plan)
            contracts.append(contract)
            states[contract.state] = states.get(contract.state, 0) + 1
            if pid is not None:
                used[pid] = used.get(pid, 0) + 1
                plan.keys_by_provider.setdefault(pid, []).append(
                    contract.provider_token or "")

        plan.desired = len(contracts)
        plan.contracts = contracts
        plan.states = states
        plan.routed = sum(1 for c in contracts if c.provider is not None)
        plan.by_provider = {pid: n for pid, n in used.items() if n}
        plan.uncovered = [c for c in contracts
                          if c.state in (UNCOVERED, CAPACITY_LIMITED,
                                         PROVIDER_UNAVAILABLE)]
        for pid, cap in plan.capacity.items():
            cap["used"] = used.get(pid, 0)
            cap["available"] = max(0, (cap["budget"] or 0) - cap["used"])
        return plan

    def _provider_order(self, preferred: str | None,
                        fallback: str | None) -> list[str]:
        reg = self._reg()
        order: list[str] = []
        for pid in (preferred, fallback):
            if pid and pid in reg.ids() and pid not in order:
                order.append(pid)
        for pid in reg.preference_order():
            if pid not in order:
                order.append(pid)
        return order

    def _route_spot(self, und, kind, candidates, order, required_caps, used
                    ) -> tuple[RoutedContract, str | None]:
        """Route one underlying's spot key to a capable provider."""
        reg = self._reg()
        contract = RoutedContract(underlying=und, kind=kind, exchange="NSE",
                                  expiry="", instrument_type="SPOT")
        capable_but_full = capable_but_unavailable = False
        for pid in order:
            if not reg.is_enabled(pid):
                continue
            desc = reg.get(pid)
            if desc is None or not desc.supports(*required_caps):
                continue
            key = candidates.get(pid)
            if not key:
                continue
            if not self._available(pid):
                capable_but_unavailable = True
                continue
            budget = reg.budget(pid)
            if budget is not None and used.get(pid, 0) >= budget:
                capable_but_full = True
                continue
            contract.provider = pid
            contract.provider_token = key
            subscribed = self._subscribed(pid)
            if key in subscribed:
                contract.state = LIVE if self._available(pid) else STALE
            else:
                contract.state = UNSUBSCRIBED
            return contract, pid
        if capable_but_full:
            contract.state = CAPACITY_LIMITED
            contract.reason = "provider budget exhausted"
        elif capable_but_unavailable:
            contract.state = PROVIDER_UNAVAILABLE
            contract.reason = "capable provider unavailable"
        else:
            contract.state = UNCOVERED
            contract.reason = "no enabled provider has a spot key"
        return contract, None

    def _feed_key_for(self, row: dict[str, Any], provider: str) -> str | None:
        """The provider's FEED key for a catalogue row.

        The routed key must be the key the provider's stream subscribes
        with and the key the quote store is written under. That is the
        provider's own identifier for the instrument, resolved through the
        canonical identity helper — never a string-shape guess.
        """
        try:
            from app.canonical_identity import provider_quote_key
            key = provider_quote_key(row)
        except Exception:
            key = None
        if key:
            return str(key)
        return row.get("provider_symbol") or row.get("instrument_token")

    def _route_one(self, und, kind, exchange, exp, itype, by_prov, order,
                   required_caps, used, plan) -> tuple[RoutedContract, str | None]:
        reg = self._reg()
        contract = RoutedContract(underlying=und, kind=kind, exchange=exchange,
                                  expiry=exp, instrument_type=itype)
        capable_but_full = False
        capable_but_unavailable = False
        for pid in order:
            if not reg.is_enabled(pid):
                continue
            desc = reg.get(pid)
            if desc is None or not desc.supports(*required_caps):
                continue
            row = by_prov.get(pid)
            if row is None:
                continue  # provider has no catalog token for this contract
            if not self._available(pid):
                capable_but_unavailable = True
                continue
            budget = reg.budget(pid)
            if budget is not None and used.get(pid, 0) >= budget:
                capable_but_full = True
                continue
            # Selected. The routed key is the provider's FEED key — the
            # key the provider's stream subscribes with and the key the
            # quote store is written under — resolved from the catalogue
            # row, never inferred from the key's string shape.
            contract.provider = pid
            contract.provider_token = self._feed_key_for(row, pid)
            contract.tradingsymbol = row.get("tradingsymbol")
            contract.lot_size = row.get("lot_size")
            subscribed = self._subscribed(pid)
            token = contract.provider_token or ""
            if token and token in subscribed:
                contract.state = (LIVE if self._available(pid) else STALE)
            else:
                contract.state = UNSUBSCRIBED
            return contract, pid
        if capable_but_full:
            contract.state = CAPACITY_LIMITED
            contract.reason = "provider budget exhausted"
        elif capable_but_unavailable:
            contract.state = PROVIDER_UNAVAILABLE
            contract.reason = "capable provider unavailable"
        else:
            contract.state = UNCOVERED
            contract.reason = "no enabled provider has a catalog token + capability"
        return contract, None

    # -- coverage summary ----------------------------------------------------

    def coverage(self, *, expiry: str | None = None,
                 config: dict[str, Any] | None = None) -> RoutingPlan:
        """Routing plan + coverage counts (alias; states are on the plan)."""
        return self.plan(expiry=expiry, config=config)
