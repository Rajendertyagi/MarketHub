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

    def _row_score(self, row: dict[str, Any], norm_q: str) -> int:
        """Lower is better relevance for a fallback substring match."""
        if not norm_q:
            return 9
        if self._norm(row.get("underlying")) == norm_q:
            return 0                      # canonical underlying key match
        sym = self._norm(row.get("tradingsymbol"))
        # Strip the exchange prefix and series suffix for a "core symbol".
        core = re.sub(r"^(nse|bse|mcx|cde)", "", sym)
        core = re.sub(r"(eq|index|etf)$", "", core)
        if core.startswith(norm_q):
            return 1
        if norm_q in sym:
            return 2
        return 3                          # name-only match

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
    def _compact(row: dict[str, Any]) -> dict[str, Any]:
        """Canonical compact identity for tool/API output."""
        out = {
            "instrument_key": f"{row['exchange']}:{row['instrument_token']}",
            "provider": row.get("provider"),
            "provider_token": row.get("instrument_token"),
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

        # Generic fallback: substring search (normalized space-insensitive
        # match happens client-side against compact symbols).
        kw: dict[str, Any] = {"q": remainder or None, "limit": max(limit, 50)}
        if exchange:
            kw["exchange"] = exchange
        if expiry:
            kw["expiry"] = expiry
        if len(want_types) == 1:
            kw["instrument_type"] = want_types[0]
        # Generic fallback: substring search per canonical type, in
        # product-relevance order (index/equity before derivatives), so
        # SQL LIMITs cannot bury the obvious match under derivative noise.
        norm_q = self._norm(remainder)
        type_rank = {"INDEX": 0, "EQUITY": 1, "FUTURE": 2, "OPTION": 3,
                     "ETF": 4}
        order = want_types or ["INDEX", "EQUITY", "FUTURE", "OPTION", "ETF"]
        seen_keys: set[str] = set()
        for wt in order:
            # Two probes: symbol/name substring AND canonical underlying
            # key (e.g. query 'banknifty' must find NIFTYBANK-INDEX whose
            # underlying column is BANKNIFTY).
            rows = self._catalog.search(
                q=remainder or None, instrument_type=wt,
                exchange=exchange, expiry=expiry, limit=max(limit, 25))
            if remainder:
                rows = rows + [
                    r for r in self._catalog.search(
                        underlying=remainder.upper(), instrument_type=wt,
                        exchange=exchange, expiry=expiry, limit=limit)
                    if r not in rows]
            ranked = []
            for row in rows:
                if norm_q and \
                        norm_q not in self._norm(row.get("tradingsymbol")) \
                        and norm_q not in self._norm(row.get("name")) \
                        and norm_q != self._norm(row.get("underlying")):
                    continue
                ranked.append((self._row_score(row, norm_q),
                               0 if row.get("exchange") == "NSE" else 1,
                               len(self._norm(row.get("tradingsymbol"))),
                               self._compact(row)))
            ranked.sort(key=lambda t: (t[0], t[1], t[2]))
            results.extend(r for *_pref, r in ranked)
            if len(results) >= limit:
                break
        return {"query": query, "parsed": parsed,
                "count": len(results), "results": results[:limit]}

    # -- Package 20: futures discovery ---------------------------------------

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
        """
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
                         ("best_bid", "bid"), ("best_ask", "ask")):
            value = getattr(quote, src, None)
            if value is not None:
                out[dst] = value
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
