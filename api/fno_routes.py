"""F&O stock universe + workspace routes (derived catalog read model).

GET  /api/market/fno/universe?q=&limit=
     Equity underlyings with non-expired F&O contracts. Underlyings only.

GET  /api/market/fno/stock/{symbol}?window=10
     Snapshot-first workspace payload for one stock: equity identity,
     spot quote, non-expired futures, option expiries, and the bounded
     ATM ± window option contracts (CE/PE) with current canonical quotes.

POST /api/market/fno/view {symbol, window, future_count, expiry_count}
     Establishes the ACTIVE-VIEW subscription set (bounded: equity +
     futures + ATM ± window options), reconciles the live feed without
     restart, and returns the applied status. Ephemeral: switching or
     clearing the view never touches persistent subscriptions.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from api.product_routes import _json

logger = logging.getLogger(__name__)


def _quote_fields(quote: Any) -> dict[str, Any]:
    """Honest canonical quote projection (missing fields stay None)."""
    if quote is None:
        return {}
    from market.serialization import quote_to_dict
    try:
        return quote_to_dict(quote)
    except Exception:
        return {}


def build_fno_routes(catalog: Any, subscriptions: Any = None,
                     market_service: Any = None,
                     feed_provider: Any = None,
                     provider_md: Any = None) -> list[Route]:
    """Read-only universe + snapshot workspace + bounded active-view apply."""

    async def _universe(request: Request) -> Response:
        qp = request.query_params
        q = (qp.get("q") or "").strip() or None
        try:
            limit = min(int(qp.get("limit", 300)), 1000)
        except ValueError:
            return _json({"error": "limit must be an integer"}, 400)
        today = date.today().isoformat()
        rows = await asyncio.to_thread(
            catalog.fno_universe, provider="upstox", today=today,
            q=q, limit=limit)
        universe = [{
            "symbol": r["symbol"],
            "name": r["name"],
            "equity_key": r["equity_key"],
            "futures_available": (r["futures_count"] or 0) > 0,
            "options_available": (r["options_count"] or 0) > 0,
            "futures_count": r["futures_count"] or 0,
            "options_count": r["options_count"] or 0,
            "future_expiries": r["future_expiries"] or 0,
            "option_expiries": r["option_expiries"] or 0,
            "nearest_future": r["nearest_future"],
            "nearest_option": r["nearest_option"],
        } for r in rows]
        return _json({"status": "ok", "count": len(universe),
                      "universe": universe})

    async def _stock(request: Request) -> Response:
        if subscriptions is None:
            return _json({"error": "subscription service unavailable"}, 503)
        symbol = request.path_params.get("symbol", "").strip().upper()
        if not symbol:
            return _json({"error": "symbol is required"}, 400)
        qp = request.query_params
        try:
            window = min(max(int(qp.get("window", 10)), 0), 25)
            future_count = min(max(int(qp.get("futures", 2)), 0), 3)
            expiry_count = min(max(int(qp.get("expiries", 1)), 0), 2)
        except ValueError:
            return _json({"error": "window/futures/expiries must be integers"},
                         400)
        try:
            ws = await asyncio.to_thread(
                subscriptions.workspace_contracts, symbol,
                future_count=future_count,
                option_expiry_count=expiry_count,
                strikes_below=window, strikes_above=window)
        except Exception as exc:
            logger.exception("fno workspace failed for %s", symbol)
            return _json({"error": f"workspace failed: "
                                   f"{type(exc).__name__}"}, 500)
        # Snapshot-first: attach current canonical quotes (MarketService
        # state — may be last-session; nothing fabricated).
        def _q(key):
            if market_service is None or "|" not in key:
                return None
            exchange = key.split("|", 1)[0].rsplit("_", 1)[0]
            try:
                return _quote_fields(
                    market_service.get_quote_now(exchange, key))
            except Exception:
                return None
        ws["spot_quote"] = _q(ws["equity_key"]) if ws["equity_key"] else None
        for f in ws["futures"]:
            f["quote"] = _q(f["key"])
        # Provider chain snapshot (same canonical path the Option Chain
        # page uses — returns last-session OI/LTP/IV/greeks even when
        # the market is closed). Best-effort: never blocks the workspace.
        chain_note = None
        if provider_md is not None and ws["equity_key"]:
            try:
                snap = await provider_md.option_chain(
                    instrument_key=ws["equity_key"], exchange="NSE",
                    tradingsymbol=symbol,
                    expiry=ws.get("selected_expiry") or "")
                if snap is not None and getattr(snap, "strikes", None):
                    by_strike: dict[float, tuple[Any, Any]] = {}
                    for s in snap.strikes:
                        by_strike[s.strike] = (s.call, s.put)
                    matched = 0
                    for o in ws["options"]:
                        call, put = by_strike.get(
                            float(o["strike"]) if o["strike"] is not None
                            else None, (None, None))
                        leg = call if o.get("option_type") == "CE" else put
                        if leg is None:
                            continue
                        q = {
                            "ltp": getattr(leg, "ltp", None),
                            "bid": getattr(leg, "bid", None),
                            "ask": getattr(leg, "ask", None),
                            "oi": getattr(leg, "oi", None),
                            "previous_oi": getattr(leg, "previous_oi", None),
                            "oi_change": getattr(leg, "oi_change", None),
                            "volume": getattr(leg, "volume", None),
                            "iv": getattr(leg, "iv", None),
                            "greeks": getattr(leg, "delta", None) and {
                                "delta": getattr(leg, "delta", None),
                                "gamma": getattr(leg, "gamma", None),
                                "theta": getattr(leg, "theta", None),
                                "vega": getattr(leg, "vega", None),
                            } or None,
                        }
                        if any(v is not None for v in q.values()):
                            o["quote"] = {**(o.get("quote") or {}), **{
                                k: v for k, v in q.items() if v is not None}}
                            matched += 1
                    if matched:
                        chain_note = (
                            f"option quotes from provider chain snapshot "
                            f"({matched} legs, last session)")
            except Exception as exc:
                chain_note = None
                logger.info("fno chain snapshot unavailable for %s: %s",
                            symbol, type(exc).__name__)
        for o in ws["options"]:
            o.setdefault("quote", None)
        if chain_note:
            ws.setdefault("notes", []).append(chain_note)
        ws.pop("_keys_by_provider", None)
        return _json({"status": "ok", **ws})

    async def _view(request: Request) -> Response:
        if subscriptions is None or feed_provider is None:
            return _json({"error": "subscription service unavailable"}, 503)
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        symbol = (body or {}).get("symbol", "")
        if not isinstance(symbol, str) or not symbol.strip():
            return _json({"error": "symbol is required"}, 400)
        window = min(max(int((body or {}).get("window", 10)), 0), 25)
        future_count = min(max(int((body or {}).get("future_count", 2)), 0), 3)
        expiry_count = min(max(int((body or {}).get("expiry_count", 1)), 0), 2)
        try:
            ws = await asyncio.to_thread(
                subscriptions.workspace_contracts, symbol.strip().upper(),
                future_count=future_count,
                option_expiry_count=expiry_count,
                strikes_below=window, strikes_above=window)
            keys = ws.pop("_keys_by_provider", {})
            subscriptions.set_active_view(keys)
            outcome = await subscriptions.reconcile(feed_provider)
        except Exception as exc:
            logger.exception("fno active view failed for %s", symbol)
            return _json({"error": f"view apply failed: "
                                   f"{type(exc).__name__}"}, 500)
        return _json({
            "status": "ok",
            "symbol": ws["symbol"],
            "active_view": {p: len(v) for p, v in keys.items()},
            "resolved_count": len(outcome["resolved"]["contracts"]),
            "apply": outcome["apply"],
        })

    return [
        Route("/api/market/fno/universe", endpoint=_universe,
              methods=["GET"]),
        Route("/api/market/fno/stock/{symbol}", endpoint=_stock,
              methods=["GET"]),
        Route("/api/market/fno/view", endpoint=_view, methods=["POST"]),
    ]
