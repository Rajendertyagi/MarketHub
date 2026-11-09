"""Subscription service: DB preferences → resolved contracts → runtime apply.

Design contract (see package docstring):

* CODE owns canonical definitions: the 8-index registry
  (``app.market_indices.MAJOR_INDICES``) and the instruments catalog.
* DB owns user preferences (enabled indices/stocks, derivative rules).
* The resolver converts preferences into concrete provider instrument
  keys using the CATALOG — expired contracts roll over automatically
  (``current`` = first expiry >= today), and no strikes are ever
  fabricated (only catalog-listed contracts resolve).
* Runtime reconciliation diffs the resolved desired set against each
  live feed's current desired set and applies add/remove mutations —
  no restart, no relogin.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

from app.market_indices import MAJOR_INDICES

logger = logging.getLogger(__name__)


def _active_manager() -> Any:
    """The active lease manager (the owner of the subscribed set)."""
    try:
        from app.subscriptions.manager import get_active_manager
        return get_active_manager()
    except Exception:
        return None

# Safety cap: reject resolutions that would subscribe more than this many
# instruments per provider (protects broker feed limits; surfaced honestly
# in API/WebUI instead of silently truncating).
# NOTE: there is deliberately NO application-level instrument cap here.
# The provider's VERIFIED limit (providers.capacity) is the ceiling, and it
# is enforced by the subscription manager - not by this preference layer.
# A demand that exceeds a provider's ceiling is reported as
# ``capacity_limited`` with the reason, never raised away here (one
# oversized provider must not abort the whole reconciliation).

# Provider runtime states that mean the provider CANNOT serve a request
# right now (not authenticated / not started / stopped / failed).
_UNAVAILABLE_STATES = frozenset({
    "stopped", "not_started", "not started", "auth_required",
    "unauthorized", "unauthenticated", "failed", "error", "disconnected",
    "not_configured", "disabled", "inactive", "no_session", "login_required",
})


class SubscriptionLimitError(RuntimeError):
    """A resolution exceeded the per-provider subscription safety cap."""


def _today_iso() -> str:
    return date.today().isoformat()


def _provider_of(key: str, catalog: Any = None) -> str:
    """Feed-provider routing for a concrete instrument key.

    Upstox keys embed a pipe segment (``NSE_INDEX|Nifty 50``); Fyers keys
    are ``EXCH:SYMBOL``. Mirrors app.market_data._infer_provider semantics
    without importing it (no cross-module dependency).
    """
    if catalog is not None:
        try:
            from providers.registry import get_registry
            provider = get_registry().provider_for_key(key, catalog=catalog)
            if provider:
                return str(provider)
        except Exception:
            pass
    if "|" in key:
        return "upstox"
    if ":" in key:
        return "fyers"
    return "upstox"


class SubscriptionService:
    """One owner for subscription preferences, resolution, and runtime apply."""

    def __init__(
        self,
        store: Any,
        catalog: Any,
        spot_provider: Callable[[str, str], Any] | None = None,
        registry: Any = None,
        identity_resolver: Any = None,
        status_provider: Callable[[str], str | None] | None = None,
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._spot_provider = spot_provider
        self._registry = registry
        # Optional canonical identity resolver (alias -> canonical id). Used
        # to expose ``canonical_id`` additively; the native ``key`` remains
        # the backend mutation contract.
        self._identity_resolver = identity_resolver
        # Optional per-provider runtime status (e.g. the source manager's
        # state). Used to prefer a provider that can actually serve a
        # request; NEVER a hardcoded provider.
        self._status_provider = status_provider
        # Last reconciliation result (per provider), for status projection.
        self._last_apply: dict[str, Any] = {}
        # The lease manager this service applies its demand through. It
        # must share THIS service's catalogue: a manager left over from
        # another owner would resolve the demand (and the tick metadata)
        # against the wrong catalogue.
        self._manager = self._bind_manager()
        # ACTIVE-VIEW owner: ephemeral per-runtime keys required by the
        # currently open F&O workspace (spot + futures + bounded option
        # window). NEVER persisted — the desired set is the UNION of
        # persistent/base resolution and this owner, so switching or
        # closing a view can never unsubscribe a persistent key.
        self._active_view: dict[str, list[str]] = {}
        # ANALYTICS-UNIVERSE owner: ephemeral, additive. Holds the cash-equity
        # instrument keys required by the currently selected analytics universe
        # (Breadth / Sector Heatmap / Market Map). Never persisted; replaced on
        # each universe switch. Union semantics keep baseline/persistent/F&O
        # owners untouched, so leaving analytics views (or switching universe)
        # shrinks this owner back to zero without ever unsubscribing a key
        # another owner still needs.
        self._analytics_universe: dict[str, list[str]] = {}
        # DERIVATIVES-UNIVERSE owner: ephemeral, additive. Holds the routed
        # instrument keys for the whole canonical F&O universe selected by
        # FnoCoverageService (apply/release). Never persisted; replaced on each
        # apply. Union semantics keep baseline/persistent/F&O active-view and
        # analytics owners untouched, so releasing F&O coverage shrinks this
        # owner back to zero without ever unsubscribing a key another owner
        # still needs.
        self._derivatives_universe: dict[str, list[str]] = {}

    # -- provider registry (lazy; broker-neutral) -------------------------------

    def _bind_manager(self) -> Any:
        """The lease manager this service applies the demand through.

        The manager must share THIS service's catalogue: a manager left
        over from another owner would resolve the demand (and the tick
        metadata) against the wrong catalogue.
        """
        active = _active_manager()
        if active is None:
            return None
        if getattr(active, "_catalog", None) is self._catalog:
            return active
        try:
            from app.subscriptions.manager import SubscriptionManager

            return SubscriptionManager(catalog=self._catalog,
                                       registry=self._registry,
                                       status_provider=self._status_provider)
        except Exception:
            return active

    @property
    def registry(self) -> Any:
        """Provider capability/capacity registry.

        Resolved lazily from the broker-neutral ``providers`` package so this
        module never imports broker adapter code. ``None`` is a valid injected
        value for tests that exercise owners without a registry.
        """
        if self._registry is None:
            from providers.registry import get_registry
            self._registry = get_registry()
        return self._registry

    # -- active view (ephemeral, additive owner) ---------------------------------

    def set_active_view(self, keys_by_provider: dict[str, list[str]]) -> None:
        """Replace the active-view key set (bounded; caller enforces limits)."""
        self._active_view = {
            p: list(dict.fromkeys(k for k in v if k))
            for p, v in (keys_by_provider or {}).items() if v
        }

    def clear_active_view(self) -> None:
        self._active_view = {}

    # -- analytics universe (ephemeral, additive owner) --------------------------

    def set_analytics_universe(self, keys_by_provider: dict[str, list[str]]) -> None:
        """Replace the analytics-universe cash-equity key set (bounded)."""
        self._analytics_universe = {
            p: list(dict.fromkeys(k for k in v if k))
            for p, v in (keys_by_provider or {}).items() if v
        }

    def clear_analytics_universe(self) -> None:
        self._analytics_universe = {}

    def analytics_universe_keys(self) -> dict[str, list[str]]:
        return {p: list(v) for p, v in self._analytics_universe.items()}

    # -- derivatives universe (ephemeral, additive owner) ----------------------

    def set_derivatives_universe(
            self, keys_by_provider: dict[str, list[str]]) -> None:
        """Replace the routed F&O universe key set (bounded).

        Keys come from ``FnoCoverageService`` routing and are already
        provider-qualified. Replacing this owner never touches the persistent,
        active-view or analytics owners.
        """
        self._derivatives_universe = {
            p: list(dict.fromkeys(k for k in v if k))
            for p, v in (keys_by_provider or {}).items() if v
        }

    def clear_derivatives_universe(self) -> None:
        self._derivatives_universe = {}

    def derivatives_universe_keys(self) -> dict[str, list[str]]:
        return {p: list(v) for p, v in self._derivatives_universe.items()}

    # -- preferences -----------------------------------------------------------

    def ensure_defaults(self) -> None:
        """Seed all 8 canonical indices enabled (idempotent).

        The canonical definition remains available even when the user
        disables an index — only the enabled flag changes.
        """
        for entry in MAJOR_INDICES:
            existing = self._store.get_md_subscription(
                category="index", key=entry["label"])
            if existing is None:
                self._store.upsert_md_subscription(
                    category="index", key=entry["label"],
                    label=entry["label"], enabled=True)

    def migrate_from_config(self, sources_cfg: dict[str, Any] | None) -> dict:
        """One-time import of config.json feed instruments (idempotent).

        Runs AT MOST ONCE per database (a completion marker is recorded in
        source_state): every configured instrument is imported as an
        index/stock subscription, then config.json is deprecated for
        runtime ownership. Later user deletions are never resurrected,
        because the import never re-runs. Returns
        {"imported": n, "skipped": n, "ran": bool}.
        """
        marker = "md_subscriptions.config_imported"
        if self._store.get_source_state("subscriptions", marker):
            return {"imported": 0, "skipped": 0, "ran": False}
        imported = skipped = 0
        # Index keys from BOTH providers map to the same canonical index
        # (Upstox ``NSE_INDEX|Nifty 50`` and Fyers ``NSE:NIFTY50-INDEX``).
        # A Fyers index key must NOT be miscategorized as a stock.
        by_key: dict[str, dict[str, str]] = {}
        for e in MAJOR_INDICES:
            for alias in (e.get("upstox_key"), e.get("fyers_symbol")):
                if alias:
                    by_key[alias] = e
        for feed in (sources_cfg or {}).values():
            if not isinstance(feed, dict):
                continue
            for instr in feed.get("instruments") or []:
                if not isinstance(instr, dict):
                    continue
                key = (instr.get("key") or "").strip()
                if not key:
                    continue
                entry = by_key.get(key)
                if entry is not None:
                    category, label = "index", entry["label"]
                    db_key = entry["label"]
                else:
                    category = "stock"
                    label = (instr.get("tradingsymbol") or key).strip()
                    db_key = key
                existing = self._store.get_md_subscription(
                    category=category, key=db_key)
                if existing is not None:
                    skipped += 1
                    continue
                self._store.upsert_md_subscription(
                    category=category, key=db_key, label=label,
                    enabled=True)
                imported += 1
        self._store.set_source_state("subscriptions", marker, "done")
        if imported:
            logger.info(
                "subscription migration: imported %d config instruments "
                "(%d already present)", imported, skipped)
        return {"imported": imported, "skipped": skipped, "ran": True}

    def _equity_provider_candidates(self, reg: Any) -> list[str]:
        """Enabled providers to try for the equity identity, best first.

        Order: the project's provider preference (a TIE-BREAK, not a routing
        gate), skipping providers whose runtime status says they are
        unavailable. A provider with no runtime status is still eligible.
        """
        try:
            enabled = list(reg.enabled_ids())
        except Exception:
            enabled = []
        try:
            preference = list(reg.preference_order())
        except Exception:
            preference = []

        def _rank(pid: str) -> int:
            try:
                return preference.index(pid)
            except ValueError:
                return len(preference)

        def _available(pid: str) -> bool:
            status: Any = None
            if self._status_provider is not None:
                try:
                    status = self._status_provider(pid)
                except Exception:
                    status = None
            if status is None:
                try:
                    status = reg.status(pid)
                except Exception:
                    status = None
            if not status:
                return True          # unknown status -> still eligible
            text = str(status).strip().lower()
            if text in _UNAVAILABLE_STATES:
                return False         # explicitly not serving right now
            return True

        ordered = sorted(enabled, key=_rank)
        return [p for p in ordered if _available(p)] + \
            [p for p in ordered if not _available(p)]

    def _index_canonical_id(self, label: str) -> str | None:
        """Canonical id for a canonical index label (registry-driven)."""
        try:
            from market.index_registry import canonical_index
        except Exception:
            return None
        entry = canonical_index(label)
        if entry is None:
            return None
        return f"{entry.exchange}:INDEX:{entry.label}"

    def _stock_canonical_id(self, key: str) -> str | None:
        """Canonical id for a stock subscription key (resolver-driven)."""
        if self._identity_resolver is None:
            return None
        try:
            return self._identity_resolver.resolve(key)
        except Exception:
            return None

    def preferences(self) -> dict[str, Any]:
        """Full preference projection for API/WebUI (canonical registry ∪ DB).

        Adds an additive ``canonical_id`` (cross-provider identity) while
        RETAINING the native ``key`` (the backend mutation contract).
        """
        rows = self._store.list_md_subscriptions()
        db_by_key = {(r["category"], r["key"]): r for r in rows}

        indices = []
        for entry in MAJOR_INDICES:
            row = db_by_key.get(("index", entry["label"]))
            indices.append({
                "label": entry["label"],
                "key": entry["upstox_key"],
                "fyers_symbol": entry["fyers_symbol"],
                "canonical_id": self._index_canonical_id(entry["label"]),
                "enabled": bool(row["enabled"]) if row else True,
                "canonical": True,
            })
        stocks = [
            {"key": r["key"], "label": r["label"] or r["key"],
             "canonical_id": self._stock_canonical_id(r["key"]),
             "enabled": bool(r["enabled"])}
            for r in rows if r["category"] == "stock"
        ]
        rules = []
        for r in self._store.list_md_derivative_rules():
            rules.append({
                "underlying": r["underlying"],
                "futures_enabled": bool(r["futures_enabled"]),
                "futures_count": r["futures_count"],
                "options_enabled": bool(r["options_enabled"]),
                "options_count": r["options_count"],
                "strikes_below": r["strikes_below"],
                "strikes_above": r["strikes_above"],
                "calls_enabled": bool(r["calls_enabled"]),
                "puts_enabled": bool(r["puts_enabled"]),
                "updated_at": r["updated_at"],
            })
        return {"indices": indices, "stocks": stocks, "derivatives": rules}

    def set_index(self, label: str, enabled: bool) -> dict[str, Any]:
        if not any(e["label"] == label for e in MAJOR_INDICES):
            raise ValueError(f"unknown canonical index: {label}")
        return self._store.upsert_md_subscription(
            category="index", key=label, label=label, enabled=enabled)

    def add_stock(self, key: str, label: str, enabled: bool = True) -> dict:
        key = (key or "").strip()
        if not key:
            raise ValueError("instrument key is required")
        return self._store.upsert_md_subscription(
            category="stock", key=key, label=(label or key).strip(),
            enabled=enabled)

    def set_stock_enabled(self, key: str, enabled: bool) -> dict:
        row = self._store.get_md_subscription(category="stock", key=key)
        if row is None:
            raise ValueError(f"unknown stock subscription: {key}")
        return self._store.upsert_md_subscription(
            category="stock", key=key, label=row["label"], enabled=enabled)

    def remove_stock(self, key: str) -> bool:
        return self._store.delete_md_subscription(category="stock", key=key)

    def set_derivative_rule(self, underlying: str, **rule: Any) -> dict:
        underlying = (underlying or "").strip().upper()
        if not underlying:
            raise ValueError("underlying is required")
        futures_count = int(rule.get("futures_count", 1))
        options_count = int(rule.get("options_count", 1))
        strikes_below = int(rule.get("strikes_below", 0))
        strikes_above = int(rule.get("strikes_above", 0))
        if futures_count not in (1, 2):
            raise ValueError("futures_count must be 1 (current) or 2 (current+next)")
        if options_count not in (1, 2):
            raise ValueError("options_count must be 1 (nearest) or 2 (nearest+next)")
        if not (0 <= strikes_below <= 50 and 0 <= strikes_above <= 50):
            raise ValueError("strikes_below/above must be within 0..50")
        if strikes_below + strikes_above > 100:
            raise ValueError("strike range too wide (max ATM ± 50)")
        return self._store.upsert_md_derivative_rule(
            underlying=underlying,
            futures_enabled=bool(rule.get("futures_enabled", False)),
            futures_count=futures_count,
            options_enabled=bool(rule.get("options_enabled", False)),
            options_count=options_count,
            strikes_below=strikes_below, strikes_above=strikes_above,
            calls_enabled=bool(rule.get("calls_enabled", True)),
            puts_enabled=bool(rule.get("puts_enabled", True)))

    def remove_derivative_rule(self, underlying: str) -> bool:
        return self._store.delete_md_derivative_rule(
            underlying=(underlying or "").strip().upper())

    # -- resolution ------------------------------------------------------------

    def _index_keys(self) -> list[dict[str, str]]:
        """Enabled canonical indices -> ONE provider-routed feed key each.

        * The key comes from the CATALOGUE (the authority on which
          provider published which identifier); the static table is only a
          fallback for an index the catalogue does not list.
        * Each canonical index is routed to ONE provider at a time - the
          first enabled provider (configuration order) that lists it. A
          contract is never subscribed on both providers.
        """
        out = []
        enabled = {r["key"] for r in self._store.list_md_subscriptions()
                   if r["category"] == "index" and r["enabled"]}
        for entry in MAJOR_INDICES:
            if entry["label"] not in enabled:
                continue
            key, provider = self._routed_index_key(entry)
            if not key:
                continue
            out.append({"key": key,
                        "label": entry["label"], "provider": provider,
                        "kind": "index", "underlying": entry["label"],
                        "expiry": None, "strike": None,
                        "option_type": None})
        return out

    def _routed_index_key(self, entry: dict[str, str]) -> tuple[str | None,
                                                               str]:
        """The routed feed key for one canonical index.

        Catalogue first, then the provider registry's configuration order.
        The static table is the documented fallback.
        """
        label = entry.get("label") or ""
        try:
            from app.market_indices import index_provider_keys
            keys = index_provider_keys(self._catalog, label)
        except Exception:
            keys = {}
        if keys:
            try:
                from providers.registry import get_registry
                reg = get_registry()
                enabled = list(reg.enabled_ids())
                preference = list(reg.preference_order())
            except Exception:
                enabled, preference = [], []
            order = [p for p in preference if p in enabled]
            order += [p for p in enabled if p not in order]
            for provider in order:
                key = keys.get(provider)
                if key:
                    return str(key), provider
            # Only a disabled provider lists it - keep the key so the
            # caller can report it as unrouted.
            provider = next(iter(keys))
            return str(keys[provider]), provider
        # Fallback: the static table (a fallback only).
        return entry.get("upstox_key"), "upstox"

    def _stock_keys(self) -> list[dict[str, str]]:
        """Enabled stocks → their concrete feed keys (provider-routed)."""
        out = []
        for r in self._store.list_md_subscriptions():
            if r["category"] == "stock" and r["enabled"]:
                key = r["key"]
                out.append({"key": key, "label": r["label"] or key,
                            "provider": _provider_of(key, self._catalog),
                            "kind": "stock", "underlying": r["label"] or key,
                            "expiry": None, "strike": None,
                            "option_type": None})
        return out

    def _unexpired_expiries(self, underlying: str, instrument_type: str,
                            count: int) -> list[str]:
        """First `count` listed expiries >= today (natural rollover).

        Served by the canonical read model
        (``market.derivatives_universe.expiries_for``), which memoizes the
        catalogue read per (underlying, class, exchange) and is invalidated
        from the catalogue-epoch boundary. Exchange-scoped so the lookup
        uses the covering index instead of scanning the whole catalogue.
        """
        exchange = self._exchange_for(underlying)
        try:
            from market.derivatives_universe import expiries_for
            expiries = expiries_for(self._catalog, underlying,
                                     instrument_type, exchange=exchange)
        except Exception:
            try:
                expiries = self._catalog.derivative_expiries(
                    underlying, instrument_type, exchange=exchange)
            except Exception:
                return []
        today = _today_iso()
        return [e for e in expiries if (e or "") >= today][:count]

    @staticmethod
    def _exchange_for(underlying: str) -> str:
        """The exchange an underlying's F&O contracts are listed on.

        Canonical index identities come from the broker-neutral registry
        (``market.index_registry``: SENSEX/BANKEX -> BSE, the NSE indices
        -> NSE), so a SENSEX rule resolves BSE contracts instead of the
        pre-existing hardcoded NSE. MarketHub's stock F&O scope is NSE
        equity derivatives (``market.derivatives_universe``) — the
        documented default for a non-index underlying.
        """
        from market.index_registry import canonical_index

        key = (underlying or "").strip().upper()
        entry = canonical_index(key)
        if entry is None:
            from app.market_indices import label_for_underlying
            label = label_for_underlying(key)
            entry = canonical_index(label) if label else None
        return entry.exchange if entry is not None else "NSE"

    @staticmethod
    def _feed_key(row: dict[str, Any]) -> str | None:
        """The provider's FEED key for a catalogue row.

        The feed key is the key the provider's stream subscribes with and
        the key the quote store is written under — the same key the
        coverage plan routes with, so the workspace and the futures board
        address the SAME contract.
        """
        try:
            from app.canonical_identity import provider_quote_key
            key = provider_quote_key(row)
        except Exception:
            key = None
        if key:
            return str(key)
        return row.get("provider_symbol") or row.get("instrument_token")

    def _option_strikes(self, underlying: str, expiry: str) -> list[dict]:
        """Option contracts for one underlying + expiry, exchange-scoped.

        The exchange is passed so the catalogue uses its covering index
        instead of scanning the whole catalogue; a catalogue that does
        not accept it (a light-weight double) is still supported.
        """
        exchange = self._exchange_for(underlying)
        try:
            return list(self._catalog.option_strikes(
                underlying, expiry, exchange=exchange) or [])
        except TypeError:
            return list(self._catalog.option_strikes(underlying, expiry) or [])

    def _all_expiries(self, underlying: str, instrument_type: str) -> list[str]:
        """All listed expiries for an underlying (exchange-scoped, memoized)."""
        exchange = self._exchange_for(underlying)
        try:
            from market.derivatives_universe import expiries_for
            return list(expiries_for(self._catalog, underlying,
                                     instrument_type, exchange=exchange))
        except Exception:
            try:
                return list(self._catalog.derivative_expiries(
                    underlying, instrument_type, exchange=exchange) or [])
            except Exception:
                return []

    def _futures_for(self, underlying: str, expiry: str) -> list[dict[str, Any]]:
        """Futures rows for one underlying + expiry, from the bulk index.

        Served from the canonical bulk futures index (ONE catalogue read
        for all expiries) — never a per-expiry search. A catalogue without
        the bulk read model (a light-weight double) falls back to the
        per-underlying search.
        """
        symbol = underlying.strip().upper()
        exchange = self._exchange_for(underlying)
        try:
            from market.derivatives_universe import futures_index
            index = futures_index(self._catalog, exchange=exchange)
        except Exception:
            index = {}
        rows = list(index.get((symbol, expiry), []))
        if rows:
            return rows
        try:
            return list(self._catalog.search(
                underlying=underlying, instrument_type="FUTURE",
                expiry=expiry, exchange=exchange, limit=50) or [])
        except TypeError:
            return list(self._catalog.search(
                underlying=underlying, instrument_type="FUTURE",
                expiry=expiry, limit=50) or [])
        except Exception:
            return []

    def _select_one_provider(self, rows: list[dict[str, Any]],
                             reg: Any) -> list[dict[str, Any]]:
        """Keep ONE row per logical contract, from the routed provider.

        When both providers list the same contract (same underlying,
        expiry, strike and option type), only the row belonging to the
        SELECTED provider is kept — a logical contract is never emitted
        twice, and its data can never be attributed to the wrong provider.
        """
        by_contract: dict[tuple, dict[str, Any]] = {}
        for row in rows:
            key = (str(row.get("underlying") or "").upper(),
                   str(row.get("expiry") or ""),
                   row.get("strike"),
                   row.get("option_type"))
            current = by_contract.get(key)
            if current is None:
                by_contract[key] = row
                continue
            if self._row_provider_rank(row, reg) < \
                    self._row_provider_rank(current, reg):
                by_contract[key] = row
        return list(by_contract.values())

    def _row_provider_rank(self, row: dict[str, Any], reg: Any) -> int:
        """Provider preference rank for a catalogue row (lower wins)."""
        provider = str(row.get("provider") or "")
        try:
            order = list(reg.preference_order())
        except Exception:
            order = []
        try:
            enabled = list(reg.enabled_ids())
        except Exception:
            enabled = []
        if provider not in enabled:
            return len(order) + 2
        try:
            return order.index(provider)
        except ValueError:
            return len(order) + 1

    def _resolve_futures(self, underlying: str, count: int,
                         contracts: list[dict[str, Any]],
                         notes: list[str]) -> None:
        """Resolve futures contracts from the canonical bulk read model.

        ONE catalogue read serves every expiry (the bulk futures index),
        and ONE provider is selected per logical contract — so the same
        contract is never subscribed twice.
        """
        expiries = self._unexpired_expiries(underlying, "FUTURE", count)
        if not expiries:
            notes.append(f"{underlying}: no listed futures")
            return
        for expiry in expiries:
            rows = self._select_one_provider(
                self._futures_for(underlying, expiry), self.registry)
            for row in rows:
                key = self._feed_key(row)
                if not key:
                    continue
                contracts.append({
                    "key": key, "label": row.get("tradingsymbol") or key,
                    "provider": row.get("provider") or _provider_of(key),
                    "kind": "future", "underlying": underlying,
                    "expiry": expiry, "strike": None, "option_type": None,
                })

    def _resolve_options(self, underlying: str, count: int,
                         strikes_below: int, strikes_above: int,
                         calls: bool, puts: bool, atm: float,
                         contracts: list[dict[str, Any]],
                         notes: list[str]) -> None:
        """Resolve option contracts around the snapped LISTED ATM strike.

        ``atm`` comes from :meth:`_nearest_listed_strike` (an actual listed
        strike) — raw spot is never used for strike selection, so nothing
        is fabricated.
        """
        if not (calls or puts):
            return
        expiries = self._unexpired_expiries(underlying, "OPTION", count)
        if not expiries:
            notes.append(f"{underlying}: no listed options")
            return
        for expiry in expiries:
            rows = self._option_strikes(underlying, expiry)
            # ONE provider per logical contract: when both providers list
            # the same (expiry, strike, CE/PE), only the routed provider's
            # row is kept — never a duplicate logical contract.
            rows = self._select_one_provider(rows, self.registry)
            # Actual listed strikes only — never fabricated.
            try:
                listed = sorted({float(r["strike"]) for r in rows
                                 if r.get("strike") is not None})
            except (TypeError, ValueError):
                continue
            below = [s for s in listed if s < atm][-strikes_below:] \
                if strikes_below else []
            above = [s for s in listed if s > atm][:strikes_above] \
                if strikes_above else []
            chosen = set(below) | set(above) | ({atm} if atm in listed
                                                else set())
            if not chosen:
                continue
            for row in rows:
                try:
                    strike = float(row.get("strike")) \
                        if row.get("strike") is not None else None
                except (TypeError, ValueError):
                    continue
                otype = row.get("option_type")
                if strike not in chosen:
                    continue
                if otype == "CE" and not calls:
                    continue
                if otype == "PE" and not puts:
                    continue
                key = self._feed_key(row)
                if not key:
                    continue
                contracts.append({
                    "key": key,
                    "label": row.get("tradingsymbol") or key,
                    "provider": row.get("provider") or _provider_of(key),
                    "kind": "option", "underlying": underlying,
                    "expiry": expiry, "strike": strike,
                    "option_type": otype,
                })

    def _atm_strike(self, underlying: str) -> float | None:
        """Current spot LTP for an underlying (index or stock).

        Indices resolve through the shared canonical index path
        (``app.market_indices.resolve_spot``), which asks the CATALOGUE for
        each provider's own feed key — so SENSEX resolves on BSE exactly
        like NIFTY resolves on NSE. Stock underlyings resolve through their
        subscribed stock key. Returns None when spot is unavailable —
        callers keep the saved rule and report pending resolution; no ATM
        is ever fabricated.
        """
        if self._spot_provider is None:
            return None
        from app.market_indices import label_for_underlying, resolve_spot

        if label_for_underlying(underlying) is not None:
            identity_resolve = getattr(self._identity_resolver, "resolve", None)
            try:
                spot = resolve_spot(underlying, self._catalog,
                                    self._spot_provider, identity_resolve)
            except Exception:
                spot = {}
            quote = spot.get("quote") if isinstance(spot, dict) else None
            ltp = getattr(quote, "ltp", None) if quote is not None else None
            if ltp is None or ltp <= 0:
                return None
            return float(ltp)
        # Stock underlying: the RULE names the underlying, but the
        # subscription key holds the concrete instrument. Resolve via
        # exact key match, then via label match (user-facing symbols).
        row = self._store.get_md_subscription(category="stock",
                                              key=underlying)
        if row is None:
            match = next((s for s in self._store.list_md_subscriptions()
                          if s["category"] == "stock"
                          and (s["label"] or "").upper() == underlying),
                         None)
            row = match
        if row is not None and "|" in row["key"]:
            exchange, token = row["key"].split("|", 1)
            exchange = exchange.rsplit("_", 1)[0] \
                if "_" in exchange else exchange
        else:
            return None
        try:
            quote = self._spot_provider(exchange, token)
        except Exception:
            return None
        ltp = getattr(quote, "ltp", None) if quote is not None else None
        if ltp is None or ltp <= 0:
            return None
        return float(ltp)

    def _nearest_listed_strike(self, underlying: str) -> float | None:
        """Nearest listed strike to spot (ATM must be an actual contract)."""
        atm = self._atm_strike(underlying)
        if atm is None:
            return None
        return self._snap_listed_strike(underlying, atm)

    def _snap_listed_strike(self, underlying: str,
                            raw_spot: float) -> float | None:
        """Snap a raw spot LTP to the nearest ACTUAL listed strike."""
        expiries = self._unexpired_expiries(underlying, "OPTION", 1)
        if not expiries:
            return None
        rows = self._option_strikes(underlying, expiries[0])
        try:
            listed = sorted({float(r["strike"]) for r in rows
                             if r.get("strike") is not None})
        except (TypeError, ValueError):
            return None
        if not listed:
            return None
        return min(listed, key=lambda s: abs(s - raw_spot))

    def workspace_contracts(self, symbol: str, *, future_count: int = 2,
                            option_expiry_count: int = 1,
                            strikes_below: int = 10,
                            strikes_above: int = 10,
                            atm_override: float | None = None) -> dict[str, Any]:
        """Resolve concrete contracts for one F&O stock workspace.

        Snapshot-first read model: equity key, non-expired futures,
        option expiries, and the bounded ATM ± window option contracts
        (CE/PE) for live subscription. Uses ONLY catalog-listed strikes.
        Raises SubscriptionLimitError via resolve() bounds; window is
        additionally bounded by the caller.

        Catalogue reads go through the canonical read model
        (``market.derivatives_universe``) so the workspace, the coverage
        plan and the futures board share ONE cached, exchange-scoped,
        epoch-invalidated read — never a per-underlying/per-expiry search.

        ONE provider is selected per logical contract: when both providers
        list the same contract, only the routed provider's leg is emitted,
        so a logical contract is never duplicated.
        """
        symbol = (symbol or "").strip().upper()
        future_count = max(0, min(int(future_count), 3))
        option_expiry_count = max(0, min(int(option_expiry_count), 2))
        strikes_below = max(0, min(int(strikes_below), 25))
        strikes_above = max(0, min(int(strikes_above), 25))
        reg = self.registry
        out: dict[str, Any] = {
            "symbol": symbol, "equity_key": None, "futures": [],
            "option_expiries": [], "selected_expiry": None,
            "atm": None, "atm_basis": None, "options": [],
            "by_provider": {}, "notes": [],
        }
        # Equity identity (canonical catalog row). The provider is NOT
        # hardcoded: candidates are resolved through the ENABLED provider
        # registry, ordered by the project's provider preference, and a
        # provider whose runtime status says it is UNAVAILABLE is skipped.
        # Downstream the key decides the provider for the option chain, so a
        # hardcoded (or blindly first) provider would break the workspace
        # whenever that provider is logged out.
        #
        # The match is EXACT on the canonical symbol: the catalogue search
        # is a substring LIKE, so a short symbol (e.g. "LT") would
        # otherwise match an unrelated equity.
        eq = None
        for pid in self._equity_provider_candidates(reg):
            # The catalogue search is a substring LIKE, so the EXACT-symbol
            # read model is used to resolve the equity identity (a short
            # symbol such as "LT" would otherwise match an unrelated
            # equity or fall outside the limit window).
            match = None
            try:
                rows = self._catalog.search(
                    q=symbol, provider=pid, instrument_type="EQUITY",
                    exchange="NSE", limit=5)
                match = next((r for r in rows
                              if (r.get("tradingsymbol") or "").upper()
                              == symbol), None)
            except Exception:
                rows = []
            if match is None:
                try:
                    match = self._catalog.find_equity(
                        symbol, provider=pid, exchange="NSE")
                except Exception:
                    match = None
            if match is not None:
                eq = match
                break
        if eq is not None:
            out["equity_key"] = self._feed_key(eq)
        # Futures (non-expired, catalog-ordered) from the canonical bulk
        # futures index — ONE read for all expiries, exchange-scoped.
        # ONE provider per logical contract: when both providers list the
        # same (underlying, expiry), only the routed provider's row is
        # kept, so the workspace never subscribes the same contract twice.
        expiries = self._unexpired_expiries(symbol, "FUTURE", future_count)
        for expiry in expiries:
            rows = self._select_one_provider(
                self._futures_for(symbol, expiry), reg)
            for row in rows:
                key = self._feed_key(row)
                if key:
                    out["futures"].append({
                        "key": key,
                        "label": row.get("tradingsymbol") or key,
                        "expiry": expiry,
                        "provider": row.get("provider") or _provider_of(key),
                    })
        # Options: expiries + bounded ATM window contracts. Spot comes
        # from the equity's own feed key (works for never-subscribed
        # stocks); ATM snaps to an actual listed strike.
        opt_expiries = self._unexpired_expiries(symbol, "OPTION",
                                                option_expiry_count)
        # ALL listed option expiries (not just the unexpired window) for the
        # expiry picker — served by the same memoized read model.
        out["option_expiries"] = self._all_expiries(symbol, "OPTION")
        out["selected_expiry"] = opt_expiries[0] if opt_expiries else None
        atm = None
        atm_basis = None
        if atm_override is not None:
            # Explicit ATM (e.g. snapped from the provider chain snapshot's
            # own spot) — still snapped to an actual listed strike.
            atm = self._snap_listed_strike(symbol, float(atm_override))
            atm_basis = "provider_snapshot"
        if atm is None and out["equity_key"] and self._spot_provider is not None:
            eq_key = out["equity_key"]
            exchange = eq_key.split("|", 1)[0].rsplit("_", 1)[0] \
                if "|" in eq_key else "NSE"
            try:
                quote = self._spot_provider(exchange, eq_key)
            except Exception:
                quote = None
            ltp = getattr(quote, "ltp", None) if quote is not None else None
            if ltp is not None and ltp > 0:
                atm = self._snap_listed_strike(symbol, float(ltp))
                atm_basis = "spot"
        if atm is None and out["selected_expiry"]:
            # Deterministic fallback (existing project convention): middle
            # listed strike, clearly labeled — the workspace stays usable
            # when the market is closed; nothing is fabricated.
            rows = self._option_strikes(symbol, out["selected_expiry"])
            try:
                listed = sorted({float(r["strike"]) for r in rows
                                 if r.get("strike") is not None})
            except (TypeError, ValueError):
                listed = []
            if listed:
                atm = listed[len(listed) // 2]
                atm_basis = "fallback_mid_strike"
                out["notes"].append(
                    f"{symbol}: spot unavailable — option window centered "
                    f"on middle listed strike")
        out["atm"] = atm
        out["atm_basis"] = atm_basis
        if atm is not None and out["selected_expiry"]:
            contracts: list[dict[str, Any]] = []
            self._resolve_options(symbol, option_expiry_count,
                                  strikes_below, strikes_above,
                                  True, True, atm, contracts, out["notes"])
            out["options"] = contracts
        # Bounded per-provider key set for the active-view subscription.
        keys: dict[str, list[str]] = {}
        if out["equity_key"]:
            keys.setdefault(_provider_of(out["equity_key"]), [])
            keys[_provider_of(out["equity_key"])].append(out["equity_key"])
        for f in out["futures"]:
            keys.setdefault(f["provider"], [])
            if f["key"] not in keys[f["provider"]]:
                keys[f["provider"]].append(f["key"])
        for o in out["options"]:
            keys.setdefault(o["provider"], [])
            if o["key"] not in keys[o["provider"]]:
                keys[o["provider"]].append(o["key"])
        out["by_provider"] = {p: len(v) for p, v in keys.items()}
        out["_keys_by_provider"] = keys
        return out

    def resolve(self) -> dict[str, Any]:
        """DB preferences → concrete per-provider desired key set.

        Returns {"contracts": [...], "by_provider": {provider: [keys]},
        "notes": [...], "atm": {underlying: strike}, "pending": [...]}.
        Raises SubscriptionLimitError when a provider's resolved set
        exceeds the safety cap.
        """
        contracts: list[dict[str, Any]] = []
        notes: list[str] = []
        atm_map: dict[str, float] = {}
        pending: list[str] = []

        contracts.extend(self._index_keys())
        contracts.extend(self._stock_keys())

        for rule in self._store.list_md_derivative_rules():
            und = rule["underlying"]
            if not (rule["futures_enabled"] or rule["options_enabled"]):
                continue
            if rule["futures_enabled"]:
                self._resolve_futures(und, rule["futures_count"],
                                      contracts, notes)
            if rule["options_enabled"]:
                # ATM snapping: the ATM strike must be an actual listed
                # contract; strikes resolve around the listed ATM. Spot
                # unavailable → rule stays saved, resolution pending.
                listed_atm = self._nearest_listed_strike(und)
                if listed_atm is None:
                    pending.append(und)
                    continue
                atm_map[und] = listed_atm
                self._resolve_options(
                    und, rule["options_count"],
                    rule["strikes_below"], rule["strikes_above"],
                    rule["calls_enabled"], rule["puts_enabled"],
                    listed_atm, contracts, notes)

        # ACTIVE-VIEW union: ephemeral workspace keys join the desired set
        # additively. Persistent/base keys are untouched; closing the view
        # simply shrinks this owner back to zero.
        view_count = sum(len(v) for v in self._active_view.values())
        if view_count:
            for provider, keys in self._active_view.items():
                for key in keys:
                    contracts.append({
                        "key": key, "label": key,
                        "provider": provider if provider in ("upstox",
                                                             "fyers")
                        else _provider_of(key),
                        "kind": "active_view",
                        "underlying": None, "expiry": None,
                        "strike": None, "option_type": None,
                    })

        # ANALYTICS-UNIVERSE union: cash-equity keys for the selected analytics
        # universe join the desired set additively. Switching universe replaces
        # this owner; leaving analytics views shrinks it to zero. Baseline
        # indices / persistent / F&O active-view owners are never affected.
        ana_count = sum(len(v) for v in self._analytics_universe.values())
        if ana_count:
            for provider, keys in self._analytics_universe.items():
                for key in keys:
                    contracts.append({
                        "key": key, "label": key,
                        "provider": provider if provider in ("upstox", "fyers")
                        else _provider_of(key),
                        "kind": "analytics",
                        "underlying": None, "expiry": None,
                        "strike": None, "option_type": None,
                    })

        # DERIVATIVES-UNIVERSE union: the routed canonical F&O universe joins
        # the desired set additively. Applying coverage replaces this owner;
        # releasing shrinks it to zero. Baseline indices / persistent stocks /
        # derivative rules / active-view / analytics owners are never affected.
        der_count = sum(len(v) for v in self._derivatives_universe.values())
        if der_count:
            for provider, keys in self._derivatives_universe.items():
                for key in keys:
                    contracts.append({
                        "key": key, "label": key,
                        "provider": provider if provider in ("upstox",
                                                             "fyers")
                        else _provider_of(key),
                        "kind": "derivatives",
                        "underlying": None, "expiry": None,
                        "strike": None, "option_type": None,
                    })

        by_provider: dict[str, list[str]] = {}
        for c in contracts:
            by_provider.setdefault(c["provider"], [])
            if c["key"] not in by_provider[c["provider"]]:
                by_provider[c["provider"]].append(c["key"])

        # Capacity is enforced by the subscription manager against the
        # provider's VERIFIED limit. This layer only reports the demand per
        # provider so the manager can allocate it by priority.
        overflow: dict[str, dict[str, Any]] = {}
        for provider, keys in by_provider.items():
            try:
                from providers.capacity import resolve_entitlement
                ent = resolve_entitlement(provider)
                limit = ent.symbols
            except Exception:
                limit = None
            if limit is not None and len(keys) > limit:
                overflow[provider] = {
                    "requested": len(keys),
                    "verified_limit": limit,
                    "reason": "demand exceeds the provider's verified "
                              "subscription limit",
                }

        return {"contracts": contracts, "by_provider": by_provider,
                "notes": notes, "atm": atm_map, "pending": pending,
                "capacity_overflow": overflow}

    # -- runtime reconciliation -------------------------------------------------

    def _metadata_for_keys(self, keys: list[str]) -> dict[str,
                                                           tuple[str, str]]:
        """Feed tick-normalization metadata for concrete instrument keys.

        Upstox runtime keys (``NSE_FO|42631``) carry the exchange in the
        segment prefix; the trading symbol comes from the catalog when
        available. Without this metadata the feed drops every tick for a
        runtime-subscribed key as an unknown instrument.
        """
        meta: dict[str, tuple[str, str]] = {}
        for key in keys:
            if "|" not in key:
                continue
            seg = key.split("|", 1)[0]
            exchange = seg.split("_", 1)[0]
            sym = None
            try:
                row = self._catalog.get("upstox", key)
                if row and row.get("tradingsymbol"):
                    sym = row["tradingsymbol"]
            except Exception:
                sym = None
            if sym:
                meta[key] = (exchange, sym)
        return meta

    async def reconcile(
        self, feed_provider: Callable[[str], Any],
    ) -> dict[str, Any]:
        """Diff resolved desired set vs live feeds; apply add/remove.

        ``feed_provider(name)`` returns the live feed for a provider name
        (or None). Preference durability NEVER depends on feed state: a
        failed/unavailable feed records runtime_applied=False and the DB
        preference stands; the next reconcile (or restart) retries.
        """
        resolved = self.resolve()
        results: dict[str, Any] = {}
        # The SUBSCRIBED set has ONE owner: the lease manager. It applies
        # the demand with the provider's capacity, the runtime
        # availability and the failover rules; a second applier with its
        # own view of the demand would unsubscribe what the other one
        # subscribed (the symptom: a live provider's demand-derived
        # instruments disappearing after an API-triggered apply).
        manager = getattr(self, "_manager", None) or _active_manager()
        if manager is not None:
            manager.apply_demand(self.lease_requests())
            # The caller's feed lookup wins: the manager applies the demand
            # to the feeds THIS reconcile is about.
            outcome = await manager.reconcile(feed_provider)
            results = dict(outcome.get("results") or {})
            self._last_apply = {
                "at": datetime.now(timezone.utc).isoformat(),
                "desired_total": len(resolved["contracts"]),
                "results": results,
                "pending": resolved["pending"],
                "notes": resolved["notes"],
            }
            return {"resolved": resolved, "apply": self._last_apply}
        # Every provider with a resolved desired set appears in the result —
        # including ones whose feed is unavailable (honest, not hidden).
        all_providers = set(resolved["by_provider"]) | {"upstox", "fyers"}
        for provider in sorted(all_providers):
            desired = resolved["by_provider"].get(provider, [])
            feed = feed_provider(provider)
            if feed is None:
                results[provider] = {"applied": False, "reason": "feed unavailable",
                                     "desired": len(desired),
                                     "added": 0, "removed": 0}
                continue
            current = set(getattr(feed, "_instrument_keys", ())
                          or getattr(feed, "_desired", ()))
            to_add = [k for k in desired if k not in current]
            to_remove = [k for k in current if k not in set(desired)]
            # Never unsub everything from a live feed: keep at least one
            # key (feed constraint), removing only keys that are replaced.
            added = removed = 0
            try:
                if to_add:
                    meta = self._metadata_for_keys(to_add)
                    added = await feed.add_instruments(to_add,
                                                       metadata=meta) or 0
                if to_remove:
                    keep = current - set(to_remove)
                    if keep:
                        removed = await feed.remove_instruments(to_remove) or 0
                    else:
                        to_remove = []
                results[provider] = {
                    "applied": True, "desired": len(desired),
                    "added": added, "removed": len(to_remove)}
            except Exception as exc:
                results[provider] = {
                    "applied": False, "reason": type(exc).__name__,
                    "desired": len(desired), "added": added,
                    "removed": removed}
        self._last_apply = {
            "at": datetime.now(timezone.utc).isoformat(),
            "desired_total": len(resolved["contracts"]),
            "results": results,
            "pending": resolved["pending"],
            "notes": resolved["notes"],
        }
        return {"resolved": resolved, "apply": self._last_apply}

    def lease_requests(self) -> list[dict[str, Any]]:
        """The DEMAND as lease requests (the manager's input).

        Every concrete contract the preferences resolve to is a demand -
        nothing else is ever subscribed. The owner/priority says how
        important the demand is (baseline coverage, an open workspace,
        analytics).
        """
        try:
            from app.subscriptions.manager import (
                OWNER_OTHER,
                OWNER_REQUIRED,
                OWNER_WORKSPACE,
                PRIORITY_OTHER,
                PRIORITY_REQUIRED,
                PRIORITY_WORKSPACE,
            )
        except Exception:
            OWNER_OTHER = OWNER_REQUIRED = OWNER_WORKSPACE = "other"
            PRIORITY_OTHER = PRIORITY_REQUIRED = PRIORITY_WORKSPACE = 10
        resolved = self.resolve()
        out: list[dict[str, Any]] = []
        for contract in resolved["contracts"]:
            kind = contract.get("kind")
            if kind in ("index", "stock"):
                owner_kind, priority = OWNER_REQUIRED, PRIORITY_REQUIRED
            elif kind in ("future", "option"):
                owner_kind, priority = OWNER_REQUIRED, PRIORITY_REQUIRED
            elif kind == "active_view":
                owner_kind, priority = OWNER_WORKSPACE, PRIORITY_WORKSPACE
            else:
                owner_kind, priority = OWNER_OTHER, PRIORITY_OTHER
            out.append({
                "key": contract["key"],
                "provider": contract.get("provider"),
                "canonical_id": contract.get("canonical_id"),
                "owner_kind": owner_kind,
                "owner_id": owner_kind,
                "priority": priority,
                "kind": kind,
            })
        return out

    def status(self) -> dict[str, Any]:
        """Read-only health projection for Test Center (no secrets)."""
        prefs = self.preferences()
        try:
            resolved = self.resolve()
            resolved_count = len(resolved["contracts"])
            by_provider = {p: len(k) for p, k in
                           resolved["by_provider"].items()}
            pending = resolved["pending"]
            notes = resolved["notes"]
        except SubscriptionLimitError as exc:
            resolved_count = None
            by_provider = {}
            pending = []
            notes = [str(exc)]
        applied = self._last_apply.get("results", {}) \
            if self._last_apply else {}
        return {
            "indices_enabled": sum(1 for i in prefs["indices"] if i["enabled"]),
            "indices_total": len(prefs["indices"]),
            "stocks_enabled": sum(1 for s in prefs["stocks"] if s["enabled"]),
            "rules": len(prefs["derivatives"]),
            "resolved_count": resolved_count,
            "by_provider": by_provider,
            "pending": pending,
            "notes": notes,
            "analytics": {p: len(k) for p, k in self._analytics_universe.items()},
            "last_apply": self._last_apply,
            "applied": applied,
        }
