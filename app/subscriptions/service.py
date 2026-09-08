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
from datetime import date, datetime, timezone
from typing import Any, Callable

from app.market_indices import MAJOR_INDICES

logger = logging.getLogger(__name__)

# Safety cap: reject resolutions that would subscribe more than this many
# instruments per provider (protects broker feed limits; surfaced honestly
# in API/WebUI instead of silently truncating).
MAX_INSTRUMENTS_PER_PROVIDER = 500


class SubscriptionLimitError(RuntimeError):
    """A resolution exceeded the per-provider subscription safety cap."""


def _today_iso() -> str:
    return date.today().isoformat()


def _provider_of(key: str) -> str:
    """Feed-provider routing for a concrete instrument key.

    Upstox keys embed a pipe segment (``NSE_INDEX|Nifty 50``); Fyers keys
    are ``EXCH:SYMBOL``. Mirrors app.market_data._infer_provider semantics
    without importing it (no cross-module dependency).
    """
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
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._spot_provider = spot_provider
        # Last reconciliation result (per provider), for status projection.
        self._last_apply: dict[str, Any] = {}
        # ACTIVE-VIEW owner: ephemeral per-runtime keys required by the
        # currently open F&O workspace (spot + futures + bounded option
        # window). NEVER persisted — the desired set is the UNION of
        # persistent/base resolution and this owner, so switching or
        # closing a view can never unsubscribe a persistent key.
        self._active_view: dict[str, list[str]] = {}

    # -- active view (ephemeral, additive owner) ---------------------------------

    def set_active_view(self, keys_by_provider: dict[str, list[str]]) -> None:
        """Replace the active-view key set (bounded; caller enforces limits)."""
        self._active_view = {
            p: list(dict.fromkeys(k for k in v if k))
            for p, v in (keys_by_provider or {}).items() if v
        }

    def clear_active_view(self) -> None:
        self._active_view = {}

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
        by_key = {e["upstox_key"]: e for e in MAJOR_INDICES}
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

    def preferences(self) -> dict[str, Any]:
        """Full preference projection for API/WebUI (canonical registry ∪ DB)."""
        rows = self._store.list_md_subscriptions()
        db_by_key = {(r["category"], r["key"]): r for r in rows}

        indices = []
        for entry in MAJOR_INDICES:
            row = db_by_key.get(("index", entry["label"]))
            indices.append({
                "label": entry["label"],
                "key": entry["upstox_key"],
                "fyers_symbol": entry["fyers_symbol"],
                "enabled": bool(row["enabled"]) if row else True,
                "canonical": True,
            })
        stocks = [
            {"key": r["key"], "label": r["label"] or r["key"],
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
        """Enabled canonical indices → concrete Upstox feed keys."""
        out = []
        enabled = {r["key"] for r in self._store.list_md_subscriptions()
                   if r["category"] == "index" and r["enabled"]}
        for entry in MAJOR_INDICES:
            if entry["label"] in enabled:
                out.append({"key": entry["upstox_key"],
                            "label": entry["label"], "provider": "upstox",
                            "kind": "index", "underlying": entry["label"],
                            "expiry": None, "strike": None,
                            "option_type": None})
        return out

    def _stock_keys(self) -> list[dict[str, str]]:
        """Enabled stocks → their concrete feed keys (provider-routed)."""
        out = []
        for r in self._store.list_md_subscriptions():
            if r["category"] == "stock" and r["enabled"]:
                key = r["key"]
                out.append({"key": key, "label": r["label"] or key,
                            "provider": _provider_of(key),
                            "kind": "stock", "underlying": r["label"] or key,
                            "expiry": None, "strike": None,
                            "option_type": None})
        return out

    def _unexpired_expiries(self, underlying: str, instrument_type: str,
                            count: int) -> list[str]:
        """First `count` listed expiries >= today (natural rollover)."""
        try:
            expiries = self._catalog.derivative_expiries(
                underlying, instrument_type)
        except Exception:
            return []
        today = _today_iso()
        return [e for e in expiries if (e or "") >= today][:count]

    def _resolve_futures(self, underlying: str, count: int,
                         contracts: list[dict[str, Any]],
                         notes: list[str]) -> None:
        expiries = self._unexpired_expiries(underlying, "FUTURE", count)
        if not expiries:
            notes.append(f"{underlying}: no listed futures")
            return
        for expiry in expiries:
            rows = self._catalog.search(underlying=underlying,
                                        instrument_type="FUTURE",
                                        expiry=expiry, limit=10)
            for row in rows or []:
                key = row.get("provider_symbol") or row.get("instrument_token")
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
            rows = self._catalog.option_strikes(underlying, expiry) or []
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
                key = row.get("provider_symbol") \
                    or row.get("instrument_token")
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

        Indices resolve through the canonical registry key; stock
        underlyings resolve through their subscribed stock key. Returns
        None when spot is unavailable — callers keep the saved rule and
        report pending resolution; no ATM is ever fabricated.
        """
        if self._spot_provider is None:
            return None
        entry = next((e for e in MAJOR_INDICES
                      if e["label"] == underlying), None)
        if entry is not None:
            exchange, token = (entry["upstox_exchange"], entry["upstox_key"])
        else:
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
        rows = self._catalog.option_strikes(underlying, expiries[0]) or []
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
        """
        symbol = (symbol or "").strip().upper()
        future_count = max(0, min(int(future_count), 3))
        option_expiry_count = max(0, min(int(option_expiry_count), 2))
        strikes_below = max(0, min(int(strikes_below), 25))
        strikes_above = max(0, min(int(strikes_above), 25))
        out: dict[str, Any] = {
            "symbol": symbol, "equity_key": None, "futures": [],
            "option_expiries": [], "selected_expiry": None,
            "atm": None, "atm_basis": None, "options": [],
            "by_provider": {}, "notes": [],
        }
        # Equity identity (canonical catalog row).
        eq_rows = self._catalog.search(q=symbol, provider="upstox",
                                       instrument_type="EQUITY", limit=5)
        eq = next((r for r in eq_rows
                   if (r.get("tradingsymbol") or "").upper() == symbol), None)
        if eq is not None:
            out["equity_key"] = eq.get("instrument_token")
        # Futures (non-expired, catalog-ordered).
        expiries = self._unexpired_expiries(symbol, "FUTURE", future_count)
        for expiry in expiries:
            for row in self._catalog.search(underlying=symbol,
                                            instrument_type="FUTURE",
                                            expiry=expiry, limit=5):
                key = row.get("provider_symbol") \
                    or row.get("instrument_token")
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
        out["option_expiries"] = self._catalog.derivative_expiries(
            symbol, "OPTION")
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
            rows = self._catalog.option_strikes(symbol,
                                                out["selected_expiry"]) or []
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

        by_provider: dict[str, list[str]] = {}
        for c in contracts:
            by_provider.setdefault(c["provider"], [])
            if c["key"] not in by_provider[c["provider"]]:
                by_provider[c["provider"]].append(c["key"])

        for provider, keys in by_provider.items():
            if len(keys) > MAX_INSTRUMENTS_PER_PROVIDER:
                raise SubscriptionLimitError(
                    f"subscription limit exceeded: {len(keys)} instruments "
                    f"resolved for {provider} (max "
                    f"{MAX_INSTRUMENTS_PER_PROVIDER})")

        return {"contracts": contracts, "by_provider": by_provider,
                "notes": notes, "atm": atm_map, "pending": pending}

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
            "last_apply": self._last_apply,
            "applied": applied,
        }
