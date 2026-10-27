"""Unified market-intelligence service — ONE semantic implementation.

Backs WebUI search, REST routes, MCP tools, and the Chat agent alike:

  * natural-language-ish instrument search ("reliance future",
    "nifty 25000 ce", "banknifty")
  * derivatives discovery (futures contracts, option expiries/strikes)
  * option-chain assembly with deterministic ATM, strike windows, and
    scope-labeled analytics

No broker raw schemas cross this boundary. Identity is canonical:
(provider, instrument_token) plus human fields (tradingsymbol, underlying,
expiry, strike, option_type).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

_CANONICAL_TYPES = ("INDEX", "EQUITY", "ETF", "FUTURE", "OPTION")
_TYPE_ALIASES = {
    "index": "INDEX", "indices": "INDEX", "idx": "INDEX",
    "equity": "EQUITY", "equities": "EQUITY", "stock": "EQUITY",
    "stocks": "EQUITY", "cash": "EQUITY", "eq": "EQUITY",
    "etf": "ETF",
    "future": "FUTURE", "futures": "FUTURE", "fut": "FUTURE",
    "option": "OPTION", "options": "OPTION", "opt": "OPTION",
}
_CE_PE = {"ce": "CE", "pe": "PE", "call": "CE", "put": "PE"}

_DEFAULT_WINDOW = 10
_MAX_WINDOW = 100


class MarketIntel:
    """Search + derivatives discovery + chain assembly over the catalog."""

    def __init__(self, catalog: Any,
                 spot_provider: Callable[[str, str], Any] | None = None,
                 identity_resolver: Any = None,
                 ) -> None:
        # catalog: app.instruments.InstrumentCatalog
        # spot_provider: SYNC (exchange, instrument_token) -> quote|None
        #   (MarketService.get_quote_now) - safe lock-free state read.
        # identity_resolver: app.instrument_identity registry - resolves
        #   any known identifier (catalog token, config key, symbol) to
        #   the MarketService storage key. Optional for tests.
        self._catalog = catalog
        self._spot_provider = spot_provider
        self._identity = identity_resolver

    def resolve_underlying(self, name: str) -> dict[str, Any] | None:
        """Public resolver: map a human underlying name ('NIFTY', 'RELIANCE') to
        its canonical catalog row.

        Returns the underlying row dict (with exchange, instrument_token,
        tradingsymbol, underlying, ...) or None when unknown. Tools use this to
        turn a symbol into the keys the data service needs.
        """
        return self._find_underlying(name)

    def _storage_key(self, exchange: str, token: str) -> tuple[str, str]:
        """Resolve (exchange, any-identifier) to the MarketService key.

        Identity resolution lives HERE so consumers never branch on
        provider specifics. Unresolvable ids pass through unchanged.
        """
        if self._identity is not None:
            resolved = self._identity.resolve(token)
            if resolved:
                return exchange, resolved
        return exchange, token

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _norm(value: str | None) -> str:
        return re.sub(r"[^a-z0-9]", "", (value or "").lower())


    def _find_underlying(self, name: str) -> dict[str, Any] | None:
        """Resolve an underlying name ('NIFTY', 'RELIANCE', 'NIFTY50') to
        its INDEX row first, then EQUITY."""
        norm = self._norm(name)
        if not norm:
            return None
        for itype in ("INDEX", "EQUITY"):
            # Targeted queries first (exact underlying key / substring),
            # then a normalized scan of that type's rows.
            for rows in (
                    self._catalog.search(underlying=name,
                                         instrument_type=itype, limit=10),
                    self._catalog.search(q=name,
                                         instrument_type=itype, limit=100),
                    self._catalog.search(instrument_type=itype, limit=5000)):
                for row in rows:
                    if norm in (self._norm(row.get("underlying")),
                                self._norm(row.get("tradingsymbol")),
                                self._norm(row.get("name"))):
                        return row
        return None

    @staticmethod
    def _provider_rank(row: dict[str, Any]) -> int:
        """Provider preference rank for a catalogue row (lower wins).

        The provider ORDER is the project's configured preference; the
        enabled set is honoured. A row from a disabled provider ranks last
        (it can never be routed to).
        """
        provider = str(row.get("provider") or "")
        try:
            from providers.registry import get_registry
            reg = get_registry()
            order = list(reg.preference_order())
            enabled = set(reg.enabled_ids())
        except Exception:
            order, enabled = [], set()
        if provider not in enabled:
            return len(order) + 2
        try:
            return order.index(provider)
        except ValueError:
            return len(order) + 1

    def _dedupe_rows(self, rows: list[dict[str, Any]]
                     ) -> list[dict[str, Any]]:
        """Keep ONE row per canonical instrument, from the routed provider.

        Cash instruments are listed by both providers, so the same real
        instrument can arrive twice. Identity is canonical (never a string
        heuristic); rows whose canonical id cannot be derived are kept as-is
        in input order, and the first-seen order is preserved for the rest.
        """
        from app.canonical_identity import canonical_id_for_row
        ranks: dict[str, int] = {}
        order: list[str] = []
        best: dict[str, dict[str, Any]] = {}
        unkeyed: list[dict[str, Any]] = []
        for row in rows or []:
            try:
                cid = canonical_id_for_row(row)
            except Exception:
                cid = None
            if not cid:
                unkeyed.append(row)
                continue
            if cid not in best:
                order.append(cid)
                best[cid] = row
                continue
            provider = str(row.get("provider") or "")
            current = str(best[cid].get("provider") or "")
            if provider not in ranks:
                ranks[provider] = self._provider_rank(row)
            if current not in ranks:
                ranks[current] = self._provider_rank(best[cid])
            if ranks[provider] < ranks[current]:
                best[cid] = row
        return [best[cid] for cid in order] + unkeyed

    @staticmethod
    def _compact(row: dict[str, Any]) -> dict[str, Any]:
        """Canonical compact identity for tool/API output.

        Carries the provider attribution AND the provider-native key, so a
        consumer can tell WHICH provider's contract a row is — and never
        mistake one provider's leg for another's.
        """
        try:
            from app.canonical_identity import (
                canonical_id_for_row,
                provider_quote_key,
            )
            native_key = provider_quote_key(row)
            canonical_id = canonical_id_for_row(row)
        except Exception:
            native_key = row.get("provider_symbol") or row.get("instrument_token")
            canonical_id = None
        out = {
            "instrument_key": f"{row['exchange']}:{row['instrument_token']}",
            "provider": row.get("provider"),
            "provider_token": row.get("instrument_token"),
            "native_key": native_key,
            "canonical_id": canonical_id,
            "symbol": row.get("tradingsymbol"),
            "display_name": row.get("name") or row.get("tradingsymbol"),
            "exchange": row.get("exchange"),
            "type": row.get("instrument_type"),
        }
        if row.get("instrument_type") in ("FUTURE", "OPTION"):
            out["underlying"] = row.get("underlying")
            out["expiry"] = row.get("expiry")
        if row.get("instrument_type") == "OPTION":
            out["strike"] = row.get("strike")
            out["option_type"] = row.get("option_type")
        if row.get("lot_size"):
            out["lot_size"] = row.get("lot_size")
        return out

    # -- Package 14: unified search -----------------------------------------

    def search(self, query: str, *, types: list[str] | None = None,
               exchange: str | None = None, expiry: str | None = None,
               limit: int = 20) -> dict[str, Any]:
        """Structured search over the catalog.

        Understands: plain symbols ("reliance"), type words ("reliance
        future"), option descriptors ("nifty 25000 ce"). Returns compact
        candidates plus how the query was parsed.
        """
        parsed: dict[str, Any] = {"raw": query}
        tokens = [t for t in re.split(r"[\s,+]+", (query or "").strip()) if t]
        want_types: list[str] = []
        for t in list(tokens):
            low = t.lower()
            if low in _TYPE_ALIASES:
                want_types.append(_TYPE_ALIASES[low])
                tokens.remove(t)
            elif low in _CE_PE:
                parsed["option_type"] = _CE_PE[low]
                tokens.remove(t)
            elif low.isdigit():
                parsed["strike"] = float(low)
                tokens.remove(t)
        if types:
            want_types = [t.upper() for t in types]
        if want_types:
            parsed["types"] = want_types

        remainder = " ".join(tokens)
        results: list[dict[str, Any]] = []

        # Option descriptor path: underlying + strike + CE/PE.
        if parsed.get("strike") and parsed.get("option_type") and remainder:
            under_row = self._find_underlying(remainder)
            if under_row is not None:
                und_key = under_row.get("underlying") \
                    or under_row.get("tradingsymbol")
                parsed["underlying"] = und_key
                rows = self._catalog.search(
                    underlying=und_key, instrument_type="OPTION",
                    strike=parsed["strike"],
                    option_type=parsed["option_type"], limit=50)
                results.extend(self._compact(r) for r in rows)
                return {"query": query, "parsed": parsed,
                        "count": len(results), "results": results}

        # Type-word path: e.g. "reliance future".
        if want_types and remainder:
            under_row = self._find_underlying(remainder)
            if under_row is not None:
                und_key = under_row.get("underlying") \
                    or under_row.get("tradingsymbol")
                parsed["underlying"] = und_key
                for wt in want_types:
                    if wt in ("FUTURE", "OPTION"):
                        rows = self._catalog.search(
                            underlying=und_key, instrument_type=wt,
                            expiry=expiry, limit=max(limit, 50))
                    else:
                        rows = self._catalog.search(
                            q=remainder, instrument_type=wt,
                            exchange=exchange, expiry=expiry,
                            limit=limit)
                    results.extend(self._compact(r) for r in rows)
                if results:
                    return {"query": query, "parsed": parsed,
                            "count": len(results),
                            "results": results[:limit]}

        # Generic fallback: ONE ranked pass over the cash classes. The read
        # model scores relevance (exact -> prefix -> contains) and prefers
        # NSE, so a SQL LIMIT can no longer bury the obvious match under
        # derivative noise; derivatives are reached through parsed intent or
        # an explicit type filter. An expiry filter is explicit derivative
        # intent, so it widens the scope instead of matching no cash row.
        ranked_types = list(want_types) or None
        if ranked_types is None and expiry:
            ranked_types = list(_CANONICAL_TYPES)
        rows = self._catalog.search(
            q=remainder or None, types=ranked_types,
            exchange=exchange, expiry=expiry,
            limit=max(limit, 25), rank=remainder or None)
        results.extend(self._compact(r) for r in self._dedupe_rows(rows))
        results = results[:limit]
        return {"query": query, "parsed": parsed,
                "count": len(results), "results": results}

    def search_exact(self, symbol: str, *, exchange: str | None = None,
                     limit: int = 20) -> dict[str, Any]:
        """Resolve EXACT symbol/underlying matches only.

        The picker's "open this instrument" path: no substring guessing and
        no ``results[0]`` substitution, so a caller that asks for one
        instrument can never land on a different instrument class.
        Untyped rows are excluded, and provider duplicates merge to the
        routed provider's row.
        """
        result: dict[str, Any] = {
            "query": symbol,
            "parsed": {"raw": symbol, "exact": True},
            "count": 0,
            "results": [],
        }
        want = (symbol or "").strip().upper()
        if not want:
            return result
        rows = [
            row for row in self._catalog.find_exact_rows(
                want, exchange=exchange)
            if str(row.get("instrument_type") or "").upper()
            in _CANONICAL_TYPES
        ]
        results = [self._compact(r) for r in self._dedupe_rows(rows)]
        result["results"] = results[:max(1, min(limit, 20))]
        result["count"] = len(result["results"])
        return result

    # -- Package 20: futures discovery ---------------------------------------

    @staticmethod
    def _derivative_exchange(under_row: dict[str, Any]) -> str | None:
        """The exchange an underlying's derivatives are listed on.

        A contract is listed by the exchange that lists its underlying
        (SENSEX -> BSE, NIFTY -> NSE), and the catalogue carries that
        exchange on the underlying's own row. Scoping the derivative reads
        by it is what makes the BSE leg resolvable at all: an unscoped
        lookup would work, but the pre-existing hardcoded ``NSE`` returned
        an empty SENSEX chain. ``None`` (unknown) stays unscoped rather
        than guessing an exchange.
        """
        exchange = str(under_row.get("exchange") or "").strip().upper()
        return exchange or None

    def futures_contracts(self, underlying: str,
                          expiry: str | None = None) -> dict[str, Any]:
        under_row = self._find_underlying(underlying)
        if under_row is None:
            return {"error": f"unknown underlying '{underlying}'"}
        und_key = under_row.get("underlying") \
            or under_row.get("tradingsymbol")
        expiries = self._catalog.derivative_expiries(und_key, "FUTURE")
        rows = self._catalog.search(underlying=und_key,
                                    instrument_type="FUTURE", limit=100)
        if expiry:
            rows = [r for r in rows if r.get("expiry") == expiry]
        rows.sort(key=lambda r: r.get("expiry") or "")
        return {
            "underlying": und_key,
            "underlying_instrument": self._compact(under_row),
            "expiries": expiries,
            "contracts": [self._compact(r) for r in rows],
        }

    # -- Packages 7/8/9/13: option chain --------------------------------------

    def option_expiries(self, underlying: str) -> dict[str, Any]:
        under_row = self._find_underlying(underlying)
        if under_row is None:
            return {"error": f"unknown underlying '{underlying}'"}
        und_key = under_row.get("underlying") \
            or under_row.get("tradingsymbol")
        return {
            "underlying": und_key,
            "underlying_instrument": self._compact(under_row),
            "expiries": self._catalog.derivative_expiries(
                und_key, "OPTION"),
        }

    def _spot_for(self, under_row: dict[str, Any]) -> tuple[
            float | None, str]:
        """Best-effort spot: live quote when available, else None.

        Returns (spot, basis) where basis explains freshness honestly.
        Known index underlyings resolve through the shared canonical index
        identities (app.market_indices — the same path the header strip
        uses), so option-chain spot and header spot can never diverge.
        """
        # Canonical index path first: ordered provider candidates with an
        # honest live/stale basis. A stale (last-session) quote is returned
        # as-is so callers can label it instead of silently substituting a
        # fallback midpoint.
        try:
            from app.market_indices import label_for_underlying, resolve_spot
            label = label_for_underlying(
                under_row.get("underlying") or under_row.get("tradingsymbol"))
            if label is not None:
                identity_resolve = None
                if self._identity is not None:
                    identity_resolve = self._identity.resolve
                spot = resolve_spot(label, self._catalog,
                                    self._spot_provider, identity_resolve)
                quote = spot.get("quote")
                ltp = getattr(quote, "ltp", None) if quote is not None \
                    else None
                if ltp is not None:
                    return float(ltp), spot.get("basis") or "live"
        except Exception:
            logger.debug("canonical index spot lookup failed",
                         exc_info=True)
        if self._spot_provider is not None:
            try:
                exch, tok = self._storage_key(
                    under_row.get("exchange"),
                    under_row.get("instrument_token"))
                quote = self._spot_provider(exch, tok)
            except Exception:
                quote = None
            if quote is not None:
                ltp = getattr(quote, "ltp", None)
                if ltp is not None:
                    return float(ltp), "live"
        return None, "unavailable"

    def option_chain(self, underlying: str, *, expiry: str | None = None,
                     window: int = _DEFAULT_WINDOW,
                     spot: float | None = None) -> dict[str, Any]:
        """Catalog-driven chain: ATM ± window strike pairs + analytics.

        ATM is the nearest ACTUAL listed strike to the underlying spot
        (live when available). Analytics are computed over the loaded
        window only and labeled as such.
        """
        window = max(1, min(int(window), _MAX_WINDOW))
        under_row = self._find_underlying(underlying)
        if under_row is None:
            return {"error": f"unknown underlying '{underlying}'"}
        und_key = under_row.get("underlying") \
            or under_row.get("tradingsymbol")

        expiries = self._catalog.derivative_expiries(und_key, "OPTION")
        if not expiries:
            return {"error": f"no listed options for '{und_key}'"}
        chosen_expiry = expiry or expiries[0]
        if chosen_expiry not in expiries:
            return {"error": f"expiry '{chosen_expiry}' not listed",
                    "available_expiries": expiries}

        contracts = self._catalog.option_strikes(und_key, chosen_expiry)
        if not contracts:
            return {"error": "empty chain", "underlying": und_key,
                    "expiry": chosen_expiry}

        strikes_sorted = sorted({c["strike"] for c in contracts
                                 if c.get("strike") is not None})

        resolved_spot = spot
        spot_basis = "explicit"
        if resolved_spot is None:
            resolved_spot, spot_basis = self._spot_for(under_row)
        if resolved_spot is None:
            # Deterministic fallback: middle listed strike, clearly labeled.
            resolved_spot = strikes_sorted[len(strikes_sorted) // 2]
            spot_basis = "fallback_mid_strike"

        atm_strike = min(strikes_sorted,
                         key=lambda s: abs(s - resolved_spot))
        atm_index = strikes_sorted.index(atm_strike)
        lo = max(0, atm_index - window)
        hi = min(len(strikes_sorted), atm_index + window + 1)
        window_strikes = strikes_sorted[lo:hi]

        by_key: dict[tuple[float, str], dict[str, Any]] = {}
        for c in contracts:
            if c.get("strike") in window_strikes and c.get("option_type"):
                entry = self._compact(c)
                live = self._live_quote_for(entry)
                if live is not None:
                    entry["quote"] = live
                by_key[(c["strike"], c["option_type"])] = entry

        rows = []
        for strike in window_strikes:
            call = by_key.get((strike, "CE"))
            put = by_key.get((strike, "PE"))
            rows.append({"strike": strike, "atm": strike == atm_strike,
                         "call": call, "put": put})

        # Analytics over the LOADED window only (explicitly labeled).
        def _oi(entry):
            q = (entry or {}).get("quote") or {}
            return q.get("open_interest")

        call_ois = [_oi(r["call"]) for r in rows if r["call"]
                    and _oi(r["call"]) is not None]
        put_ois = [_oi(r["put"]) for r in rows if r["put"]
                   and _oi(r["put"]) is not None]
        analytics: dict[str, Any] = {"scope": "loaded_window"}
        if call_ois and put_ois:
            tot_call, tot_put = sum(call_ois), sum(put_ois)
            analytics["total_call_oi"] = tot_call
            analytics["total_put_oi"] = tot_put
            analytics["pcr_by_oi"] = (
                round(tot_put / tot_call, 4) if tot_call else None)
        for side in ("call", "put"):
            best = None
            for r in rows:
                entry = r[side]
                oi = _oi(entry)
                if oi is not None and (best is None or oi > best[1]):
                    best = (entry["strike"], oi)
            if best:
                analytics[f"highest_{side}_oi_strike"] = best[0]
                analytics[f"highest_{side}_oi"] = best[1]

        return {
            "underlying": und_key,
            "underlying_instrument": self._compact(under_row),
            "expiry": chosen_expiry,
            "expiries_available": expiries,
            "spot": resolved_spot,
            "spot_basis": spot_basis,
            "atm_strike": atm_strike,
            "window": window,
            "strikes_loaded": len(rows),
            "strikes_total_listed": len(strikes_sorted),
            "rows": rows,
            "analytics": analytics,
        }

    def _live_quote_for(self, compact: dict[str, Any]) -> dict[str, Any] | None:
        """Compact live fields for one contract; None when unavailable."""
        if self._spot_provider is None:
            return None
        try:
            exchange, token = compact["instrument_key"].split(":", 1)
            exchange, token = self._storage_key(exchange, token)
            quote = self._spot_provider(exchange, token)
        except Exception:
            return None
        if quote is None:
            return None
        out: dict[str, Any] = {}
        for src, dst in (("ltp", "ltp"), ("change", "change"),
                         ("change_percent", "change_percent"),
                         ("volume", "volume"),
                         ("open_interest", "open_interest"),
                         ("oi_change", "oi_change"),
                         ("best_bid", "bid"), ("best_ask", "ask")):
            value = getattr(quote, src, None)
            if value is not None:
                out[dst] = value
        greeks = getattr(quote, "greeks", None)
        if greeks is not None:
            for gsrc, gdst in (("iv", "iv"), ("delta", "delta"),
                               ("gamma", "gamma"), ("theta", "theta"),
                               ("vega", "vega"), ("rho", "rho")):
                value = getattr(greeks, gsrc, None)
                if value is not None:
                    out[gdst] = value
        ts = getattr(quote, "received_ts", None)
        if ts is not None:
            out["received_at"] = ts.isoformat() if hasattr(
                ts, "isoformat") else str(ts)
        return out or None

    # -- Package 15: unified snapshot ------------------------------------------

    async def snapshot(self, query: str) -> dict[str, Any]:
        """Canonical current snapshot for one instrument (search-resolved).

        Combines identity + live quote (+ depth when supported). Freshness
        is explicit; nothing is fabricated.
        """
        found = self.search(query, limit=5)
        if not found.get("results"):
            return {"error": f"no instrument matches '{query}'"}
        best = found["results"][0]
        snap: dict[str, Any] = {"instrument": best}
        exchange, token = best["instrument_key"].split(":", 1)
        exchange, token = self._storage_key(exchange, token)
        if self._spot_provider is not None:
            try:
                quote = self._spot_provider(exchange, token)
            except Exception:
                quote = None
            if quote is not None:
                from market.serialization import quote_to_dict
                snap["quote"] = quote_to_dict(quote)
                snap["freshness"] = {
                    "received_at": snap["quote"].get("received_ts"),
                    "stale": False,
                }
            else:
                snap["quote"] = None
                snap["freshness"] = {"stale": True,
                                     "reason": "no live data in market state"}
        return snap
