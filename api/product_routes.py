"""
Product API routes: instrument catalog, watchlists, alerts.

Thin adapters over EventStore-backed services. No provider names, no
secrets, no trading.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route



try:
    from app.market_data import ProviderMarketDataError
except ImportError:  # pragma: no cover
    ProviderMarketDataError = RuntimeError


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def _path_int(request: Request, key: str) -> int | None:
    """Parse an integer path parameter; None when absent or non-numeric."""
    try:
        return int(request.path_params[key])
    except (KeyError, TypeError, ValueError):
        return None


def _oi_to_dict(oi):
    return {
        "instrument_token": oi.instrument_token,
        "exchange": oi.exchange,
        "expiry": oi.expiry,
        "spot_closing_price": oi.spot_closing_price,
        "total_call_oi": oi.total_call_oi,
        "total_put_oi": oi.total_put_oi,
        "strikes": [{"strike_price": s.strike_price, "call_oi": s.call_oi, "put_oi": s.put_oi} for s in oi.strikes],
    }


def _oi_change_to_dict(oi_change):
    return {
        "instrument_token": oi_change.instrument_token,
        "exchange": oi_change.exchange,
        "expiry": oi_change.expiry,
        "spot_closing_price": oi_change.spot_closing_price,
        "total_call_change_oi": oi_change.total_call_change_oi,
        "total_put_change_oi": oi_change.total_put_change_oi,
        "days": oi_change.days,
        "strikes": [{"strike_price": s.strike_price, "call_change_oi": s.call_change_oi, "put_change_oi": s.put_change_oi} for s in oi_change.strikes],
    }


def _max_pain_to_dict(mp):
    return {
        "instrument_token": mp.instrument_token,
        "exchange": mp.exchange,
        "expiry": mp.expiry,
        "max_pain_strike": mp.max_pain_strike,
        "max_pain_value": mp.max_pain_value,
        "spot_price": mp.spot_price,
    }


def _pcr_to_dict(pcr):
    return {
        "instrument_token": pcr.instrument_token,
        "exchange": pcr.exchange,
        "expiry": pcr.expiry,
        "pcr": pcr.pcr,
        "total_put_oi": pcr.total_put_oi,
        "total_call_oi": pcr.total_call_oi,
        "spot_price": pcr.spot_price,
    }


def _news_to_dict(news):
    return {
        "instrument_token": news.instrument_token,
        "total_records": news.total_records,
        "page": news.page,
        "page_size": news.page_size,
        "articles": [{"heading": a.heading, "summary": a.summary, "thumbnail": a.thumbnail, "article_link": a.article_link, "published_time": a.published_time.isoformat() if a.published_time else None} for a in news.articles],
    }


def _holiday_to_dict(h):
    return {
        "date": h.date,
        "description": h.description,
        "holiday_type": h.holiday_type,
        "closed_exchanges": list(h.closed_exchanges),
        "open_exchanges": list(h.open_exchanges),
    }


def _session_to_dict(s):
    return {
        "exchange": s.exchange,
        "start_time": s.start_time.isoformat() if s.start_time else None,
        "end_time": s.end_time.isoformat() if s.end_time else None,
    }


def _futures_smartlist_to_dict(f):
    return {
        "asset_type": f.asset_type,
        "category": f.category,
        "metric_key": f.metric_key,
        "timestamp": f.timestamp.isoformat() if f.timestamp else None,
        "entries": [{
            "instrument_key": e.instrument_key,
            "price_current": e.price_current,
            "price_close": e.price_close,
            "price_change_abs": e.price_change_abs,
            "price_change_pct": e.price_change_pct,
            "metric_current": e.metric_current,
            "metric_previous": e.metric_previous,
            "metric_change_abs": e.metric_change_abs,
            "metric_change_pct": e.metric_change_pct,
        } for e in f.entries],
        "page_number": f.page_number,
        "page_size": f.page_size,
        "total_pages": f.total_pages,
    }


def _fii_to_dict(fii):
    return {
        "data_type": fii.data_type,
        "interval": fii.interval,
        "records": [{
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "buy_amount": r.buy_amount,
            "sell_amount": r.sell_amount,
            "buy_contracts": r.buy_contracts,
            "sell_contracts": r.sell_contracts,
            "oi_contracts": r.oi_contracts,
            "oi_amount": r.oi_amount,
        } for r in fii.records],
    }


def _dii_to_dict(dii):
    return {
        "data_type": dii.data_type,
        "interval": dii.interval,
        "records": [{
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "buy_amount": r.buy_amount,
            "sell_amount": r.sell_amount,
            "buy_contracts": r.buy_contracts,
            "sell_contracts": r.sell_contracts,
        } for r in dii.records],
    }


def _company_profile_to_dict(p):
    return {
        "isin": p.isin,
        "company_profile": p.company_profile,
        "sector": p.sector,
        "sector_market_cap_inr_crore": p.sector_market_cap_inr_crore,
        "sector_market_cap_usd_billion": p.sector_market_cap_usd_billion,
    }


def _key_ratios_to_dict(r):
    return {
        "isin": r.isin,
        "pe_ratio": r.pe_ratio,
        "pb_ratio": r.pb_ratio,
        "roe": r.roe,
        "roa": r.roa,
        "roce": r.roce,
        "ev_ebitda": r.ev_ebitda,
    }


def _corporate_action_to_dict(a):
    return {
        "action_type": a.action_type,
        "description": a.description,
        "record_date": a.record_date,
        "ex_date": a.ex_date,
        "payment_date": a.payment_date,
        "value": a.value,
    }


def _competitor_to_dict(c):
    return {
        "instrument_key": c.instrument_key,
        "symbol": c.symbol,
        "name": c.name,
        "sector": c.sector,
    }


# Fields safe to expose in the support diagnostics snapshot. Everything else
# in a source status dict (tokens, URLs, raw errors) is deliberately dropped.
_DIAGNOSTIC_SOURCE_FIELDS = (
    "name",
    "type",
    "state",
    "task_running",
    "reconnecting",
    "reconnect_count",
    "configured_instruments",
    "subscribed_count",
    "last_frame_at",
    "last_message_at",
    "last_exit_reason",
    "stop_reason",
)


def build_diagnostics_routes(version: str,
                             store: Any,
                             source_status_fn: Callable[[], list],
                             base_url_fn: Callable[[], str]) -> list[Route]:
    """GET /api/diagnostics — read-only support snapshot with NO secrets.

    Aggregates what an operator needs to report a problem: application and
    schema versions, per-source lifecycle summary, and the effective public
    base URL. Tokens, API secrets, refresh tokens, authorized WSS URLs and
    raw broker error bodies are never included.
    """

    async def _diagnostics(request: Request) -> Response:  # noqa: ARG001
        import datetime as _dt

        sources_out: list[dict[str, Any]] = []
        try:
            _raw = source_status_fn() or []
        except Exception:
            _raw = []
        for entry in _raw:
            if not isinstance(entry, dict):
                continue
            slim = {k: entry.get(k) for k in _DIAGNOSTIC_SOURCE_FIELDS
                    if entry.get(k) is not None}
            transitions = entry.get("transitions")
            if isinstance(transitions, list):
                slim["transition_count"] = len(transitions)
                slim["last_transitions"] = transitions[-3:]
            task = entry.get("task")
            if isinstance(task, dict):
                slim["task_status"] = task.get("status")
            sources_out.append(slim)
        try:
            schema_version = await asyncio.to_thread(store.schema_version)
        except Exception:
            schema_version = None
        try:
            base_url = base_url_fn()
        except Exception:
            base_url = None
        return _json({
            "service": "MarketHub",
            "version": version,
            "schema_version": schema_version,
            "public_base_url": base_url,
            "sources": sources_out,
            "generated_at": _dt.datetime.now(
                _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })

    return [Route("/api/diagnostics", endpoint=_diagnostics, methods=["GET"])]


def build_intel_routes(market_intel: Any) -> list[Route]:
    """Unified market-intelligence routes (shared with MCP/Chat logic).

    GET /api/market/search   — structured instrument search
    GET /api/futures         — futures contracts by underlying (+expiry)
    GET /api/options/chain   — catalog-driven chain when `underlying` is
                               supplied (legacy provider chain keeps working
                               when instrument_key is supplied instead)
    """

    async def _search(request: Request) -> Response:
        qp = request.query_params
        q = qp.get("q") or ""
        if not q.strip():
            return _json({"error": "q is required"}, 400)
        if len(q) > 64:
            return _json({"error": "q too long (max 64)"}, 400)
        types = [t for t in (qp.get("types") or "").split(",") if t]
        try:
            limit = min(int(qp.get("limit", 20)), 50)
        except ValueError:
            return _json({"error": "limit must be an integer"}, 400)
        result = await asyncio.to_thread(
            market_intel.search, q, types=types or None,
            exchange=qp.get("exchange") or None,
            expiry=qp.get("expiry") or None, limit=limit)
        return _json(result)

    async def _futures(request: Request) -> Response:
        underlying = request.query_params.get("underlying") or ""
        if not underlying.strip():
            return _json({"error": "underlying is required"}, 400)
        result = await asyncio.to_thread(
            market_intel.futures_contracts, underlying,
            request.query_params.get("expiry") or None)
        if "error" in result:
            return _json(result, 404)
        return _json(result)

    async def _intel_chain(request: Request) -> Response:
        qp = request.query_params
        underlying = qp.get("underlying") or ""
        if not underlying.strip():
            return _json({"error": "underlying is required"}, 400)
        window_raw = qp.get("window", "10")
        try:
            window = int(window_raw)
        except ValueError:
            return _json({"error": "window must be an integer"}, 400)
        spot_raw = qp.get("spot")
        spot = None
        if spot_raw:
            try:
                spot = float(spot_raw)
            except ValueError:
                return _json({"error": "spot must be a number"}, 400)
        result = await asyncio.to_thread(
            market_intel.option_chain, underlying,
            expiry=qp.get("expiry") or None, window=window, spot=spot)
        if "error" in result:
            status = 404 if "unknown underlying" in str(result["error"]) \
                or "not listed" in str(result["error"]) else 400
            return _json(result, status)
        return _json(result)

    return [
        Route("/api/market/search", endpoint=_search, methods=["GET"]),
        Route("/api/futures", endpoint=_futures, methods=["GET"]),
        Route("/api/options/chain/view", endpoint=_intel_chain,
              methods=["GET"]),
    ]


def build_instrument_routes(catalog: Any, store: Any = None) -> list[Route]:
    """Routes over the canonical instrument catalog."""

    async def _underlyings(request: Request) -> Response:
        if store is None:
            return _json({"error": "catalog store unavailable"}, 503)
        qp = request.query_params
        rows = await asyncio.to_thread(
            store.option_underlyings, qp.get("q") or None,
            min(int(qp.get("limit", 25)), 100))
        return _json({"underlyings": [r["underlying"] for r in rows]})

    async def _expiries(request: Request) -> Response:
        if store is None:
            return _json({"error": "catalog store unavailable"}, 503)
        underlying = request.query_params.get("underlying", "")
        if not underlying:
            return _json({"error": "underlying is required"}, 400)
        expiries = await asyncio.to_thread(store.option_expiries, underlying)
        return _json({"underlying": underlying, "expiries": expiries})

    async def _search(request: Request) -> Response:
        qp = request.query_params
        q = qp.get("q") or None
        if q and len(q) > 64:
            return _json({"error": "q too long (max 64)"}, 400)
        try:
            limit = min(int(qp.get("limit", 25)), 100)
        except ValueError:
            return _json({"error": "limit must be an integer"}, 400)
        results = await asyncio.to_thread(
            catalog.search,
            q=q,
            exchange=qp.get("exchange") or None,
            instrument_type=qp.get("type") or None,
            provider=qp.get("provider") or None,
            limit=limit,
        )
        return _json({"results": results, "count": len(results)})

    async def _sync(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        provider = (body or {}).get("provider", "upstox")
        try:
            if provider == "upstox":
                result = await asyncio.to_thread(catalog.sync_upstox)
            elif provider == "fyers":
                result = await asyncio.to_thread(catalog.sync_fyers)
            else:
                return _json({"error": "unknown provider"}, 400)
        except Exception as exc:
            return _json({"error": f"sync failed: "
                                   f"{type(exc).__name__}"}, 502)
        return _json({"status": "ok", **result})

    async def _sync_state(request: Request) -> Response:  # noqa: ARG001
        return _json({"providers": await asyncio.to_thread(
            catalog.sync_state)})

    return [
        Route("/api/instruments/search", endpoint=_search, methods=["GET"]),
        Route("/api/instruments/sync", endpoint=_sync, methods=["POST"]),
        Route("/api/instruments/sync-state", endpoint=_sync_state,
              methods=["GET"]),
        Route("/api/options/underlyings", endpoint=_underlyings,
              methods=["GET"]),
        Route("/api/options/expiries", endpoint=_expiries, methods=["GET"]),
    ]


def build_watchlist_routes(store: Any,
                           subscription: Any = None) -> list[Route]:
    """CRUD routes over persistent watchlists.

    ``subscription`` is an optional adapter exposing
    ``add(exchange, token)`` / ``remove(exchange, token)`` coroutines.
    Removal is reference-counted across ALL watchlists: the feed only
    unsubscribes when the last watchlist reference disappears.
    """

    async def _refs(store: Any, exchange: str, token: str) -> int:
        """Count references to this instrument across every watchlist."""
        total = 0
        for wl in await asyncio.to_thread(store.list_watchlists):
            items = await asyncio.to_thread(store.list_watchlist_items,
                                            wl["id"])
            total += sum(
                1 for it in items
                if it["exchange"] == exchange
                and it["instrument_token"] == token)
        return total

    async def _list(request: Request) -> Response:  # noqa: ARG001
        wls = await asyncio.to_thread(store.list_watchlists)
        out = []
        for wl in wls:
            wl = dict(wl)
            wl["items"] = await asyncio.to_thread(
                store.list_watchlist_items, wl["id"])
            out.append(wl)
        return _json({"watchlists": out})

    async def _create(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        name = (body or {}).get("name", "")
        if not isinstance(name, str) or not name.strip() or len(name) > 64:
            return _json({"error": "name is required (max 64 chars)"}, 400)
        try:
            wl = await asyncio.to_thread(store.create_watchlist,
                                         name.strip())
        except Exception:
            return _json({"error": "watchlist name already exists"}, 409)
        return _json({"status": "ok", "watchlist": wl})

    async def _patch(request: Request) -> Response:
        wl_id = _path_int(request, "watchlist_id")
        if wl_id is None:
            return _json({"error": "invalid watchlist id"}, 400)
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        name = (body or {}).get("name", "")
        if not isinstance(name, str) or not name.strip():
            return _json({"error": "name is required"}, 400)
        ok = await asyncio.to_thread(store.rename_watchlist, wl_id,
                                     name.strip())
        return _json({"status": "ok"} if ok else
                     {"error": "not found"}, 200 if ok else 404)

    async def _delete(request: Request) -> Response:
        wl_id = _path_int(request, "watchlist_id")
        if wl_id is None:
            return _json({"error": "invalid watchlist id"}, 400)
        ok = await asyncio.to_thread(store.delete_watchlist, wl_id)
        return _json({"status": "ok"} if ok else {"error": "not found"},
                     200 if ok else 404)

    async def _add_item(request: Request) -> Response:
        wl_id = _path_int(request, "watchlist_id")
        if wl_id is None:
            return _json({"error": "invalid watchlist id"}, 400)
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        exchange = (body or {}).get("exchange", "")
        token = (body or {}).get("instrument_token", "")
        symbol = (body or {}).get("tradingsymbol", "")
        if not all(isinstance(v, str) and v.strip()
                   for v in (exchange, token, symbol)):
            return _json({"error": "exchange, instrument_token and "
                                   "tradingsymbol are required"}, 400)
        item = await asyncio.to_thread(
            store.add_watchlist_item, wl_id, exchange=exchange.strip(),
            instrument_token=token.strip(), tradingsymbol=symbol.strip())
        if item is None:
            return _json({"error": "already in watchlist"}, 409)
        if subscription is not None:
            try:
                await subscription.add(exchange.strip(), token.strip())
            except Exception:
                pass  # subscription failure never breaks persistence
        return _json({"status": "ok", "item": item})

    async def _remove_item(request: Request) -> Response:
        item_id = _path_int(request, "item_id")
        if item_id is None:
            return _json({"error": "invalid item id"}, 400)
        # Capture identity before deletion for reference-counted unsub.
        removed_identity = None
        for wl in await asyncio.to_thread(store.list_watchlists):
            for it in await asyncio.to_thread(store.list_watchlist_items,
                                              wl["id"]):
                if it["id"] == item_id:
                    removed_identity = (it["exchange"],
                                        it["instrument_token"])
        ok = await asyncio.to_thread(store.remove_watchlist_item, item_id)
        if ok and removed_identity and subscription is not None:
            exchange, token = removed_identity
            refs = await _refs(store, exchange, token)
            if refs == 0:
                try:
                    await subscription.remove(exchange, token)
                except Exception:
                    pass
        return _json({"status": "ok"} if ok else {"error": "not found"},
                     200 if ok else 404)

    async def _reorder(request: Request) -> Response:
        wl_id = _path_int(request, "watchlist_id")
        if wl_id is None:
            return _json({"error": "invalid watchlist id"}, 400)
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        item_ids = (body or {}).get("item_ids")
        if not isinstance(item_ids, list) or \
                not all(isinstance(i, int) for i in item_ids):
            return _json({"error": "item_ids must be a list of ints"}, 400)
        await asyncio.to_thread(store.reorder_watchlist_items, wl_id,
                                item_ids)
        return _json({"status": "ok"})

    def _int_param(name: str) -> Callable[[Request], int]:
        return lambda r: int(r.path_params[name])

    return [
        Route("/api/watchlists", endpoint=_list, methods=["GET"]),
        Route("/api/watchlists", endpoint=_create, methods=["POST"]),
        Route("/api/watchlists/{watchlist_id}", endpoint=_patch,
              methods=["PATCH"]),
        Route("/api/watchlists/{watchlist_id}", endpoint=_delete,
              methods=["DELETE"]),
        Route("/api/watchlists/{watchlist_id}/items", endpoint=_add_item,
              methods=["POST"]),
        Route("/api/watchlists/{watchlist_id}/items/{item_id}",
              endpoint=_remove_item, methods=["DELETE"]),
        Route("/api/watchlists/{watchlist_id}/reorder", endpoint=_reorder,
              methods=["POST"]),
    ]


def build_alert_routes(store: Any, engine: Any = None) -> list[Route]:
    """CRUD + control routes for market alerts."""

    async def _list(request: Request) -> Response:  # noqa: ARG001
        alerts = await asyncio.to_thread(store.list_alerts)
        recent = engine.recent_notifications(10) if engine else []
        return _json({"alerts": alerts, "notifications": recent})

    async def _create(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        b = body or {}
        required = ("exchange", "instrument_token", "tradingsymbol",
                    "field", "operator", "threshold")
        if not all(b.get(k) is not None for k in required):
            return _json({"error": f"fields required: {required}"}, 400)
        try:
            alert = await asyncio.to_thread(store.create_alert, **{
                k: b[k] for k in required})
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)
        if engine:
            engine.reload()
        return _json({"status": "ok", "alert": alert})

    async def _delete(request: Request) -> Response:
        alert_id = _path_int(request, "alert_id")
        if alert_id is None:
            return _json({"error": "invalid alert id"}, 400)
        ok = await asyncio.to_thread(store.delete_alert, alert_id)
        if engine:
            engine.reload()
        return _json({"status": "ok"} if ok else {"error": "not found"},
                     200 if ok else 404)

    async def _rearm(request: Request) -> Response:
        alert_id = _path_int(request, "alert_id")
        if alert_id is None:
            return _json({"error": "invalid alert id"}, 400)
        ok = await asyncio.to_thread(store.rearm_alert, alert_id)
        if engine:
            engine.clear_notification(alert_id)
        return _json({"status": "ok"} if ok else {"error": "not found"},
                     200 if ok else 404)

    async def _enabled(request: Request) -> Response:
        alert_id = _path_int(request, "alert_id")
        if alert_id is None:
            return _json({"error": "invalid alert id"}, 400)
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        enabled = bool((body or {}).get("enabled"))
        ok = await asyncio.to_thread(store.set_alert_enabled, alert_id,
                                     enabled)
        if engine:
            engine.reload()
        return _json({"status": "ok"} if ok else {"error": "not found"},
                     200 if ok else 404)

    return [
        Route("/api/alerts", endpoint=_list, methods=["GET"]),
        Route("/api/alerts", endpoint=_create, methods=["POST"]),
        Route("/api/alerts/{alert_id}", endpoint=_delete, methods=["DELETE"]),
        Route("/api/alerts/{alert_id}/rearm", endpoint=_rearm,
              methods=["POST"]),
        Route("/api/alerts/{alert_id}/enabled", endpoint=_enabled,
              methods=["POST"]),
    ]


def _int_param(request: Request, name: str, default: int,
               lo: int, hi: int) -> int | None:
    """Parse a bounded int query param; return None if missing/invalid/out-of-range."""
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return None
    if val < lo or val > hi:
        return None
    return val


def build_alert_history_routes(store: Any) -> list[Route]:
    """Bounded, paginated alert trigger-history API (P2)."""

    async def _list(request: Request) -> Response:  # noqa: ARG001
        limit = _int_param(request, "limit", 50, 1, 500)
        if limit is None:
            return _json({"error": "limit must be an integer in [1, 500]"}, 400)
        offset = _int_param(request, "offset", 0, 0, 1_000_000)
        if offset is None:
            return _json({"error": "offset must be a non-negative integer"}, 400)
        alert_id_raw = request.query_params.get("alert_id")
        alert_id = None
        if alert_id_raw is not None:
            try:
                alert_id = int(alert_id_raw)
            except (TypeError, ValueError):
                return _json({"error": "alert_id must be an integer"}, 400)
        provider = request.query_params.get("provider")
        history = await asyncio.to_thread(
            store.list_alert_trigger_history, alert_id, limit, offset, provider)
        total = await asyncio.to_thread(
            store.count_alert_trigger_history, alert_id, provider)
        return _json({"history": history, "total": total,
                      "limit": limit, "offset": offset})

    async def _clear(request: Request) -> Response:  # noqa: ARG001
        alert_id_raw = request.query_params.get("alert_id")
        alert_id = None
        if alert_id_raw is not None:
            try:
                alert_id = int(alert_id_raw)
            except (TypeError, ValueError):
                return _json({"error": "alert_id must be an integer"}, 400)
        deleted = await asyncio.to_thread(
            store.clear_alert_trigger_history, alert_id)
        return _json({"status": "ok", "deleted": deleted})

    return [
        Route("/api/alerts/history", endpoint=_list, methods=["GET"]),
        Route("/api/alerts/history", endpoint=_clear, methods=["DELETE"]),
    ]






def build_market_data_routes(provider_md: Any) -> list[Route]:
    """History + option-chain routes over the provider market-data service."""

    async def _history(request: Request) -> Response:
        qp = request.query_params
        instrument_key = qp.get("instrument_key", "")
        if not instrument_key:
            return _json({"error": "instrument_key is required"}, 400)
        try:
            candles = await provider_md.history(
                instrument_key=instrument_key,
                unit=qp.get("unit", "days"),
                interval=qp.get("interval", 1),
                from_date=qp.get("from", ""),
                to_date=qp.get("to", ""))
        except ProviderMarketDataError as exc:
            return _json({"error": str(exc)}, 400)
        except Exception:
            return _json({"error": "history fetch failed"}, 502)
        from market.serialization import _to_json_value
        return _json({"candles": [_to_json_value(c) for c in candles]})

    async def _chain(request: Request) -> Response:
        qp = request.query_params
        required = ("instrument_key", "exchange", "expiry")
        if not all(qp.get(k) for k in required):
            return _json({"error": f"params required: {required}"}, 400)
        try:
            snap = await provider_md.option_chain(
                instrument_key=qp["instrument_key"],
                exchange=qp["exchange"],
                tradingsymbol=qp.get("tradingsymbol", ""),
                expiry=qp["expiry"])
        except ProviderMarketDataError as exc:
            return _json({"error": str(exc)}, 400)
        except Exception:
            return _json({"error": "option chain fetch failed"}, 502)
        from market.serialization import quote_to_dict as _q2d
        strikes = []
        for s in snap.strikes:
            strikes.append({
                "strike": s.strike, "atm": s.atm,
                "call": s.call.__dict__ if s.call else None,
                "put": s.put.__dict__ if s.put else None,
            })
        return _json({
            "instrument_token": snap.instrument_token,
            "exchange": snap.exchange,
            "tradingsymbol": snap.tradingsymbol,
            "expiry": snap.expiry,
            "spot_price": snap.spot_price,
            "atm_strike": snap.atm_strike,
            "strikes": strikes,
        })

    async def _margin(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        if not isinstance(body, dict):
            return _json({"error": "JSON body must be an object"}, 400)
        instruments = body.get("instruments")
        if not isinstance(instruments, list) or not instruments:
            return _json({"error": "instruments (non-empty list) is required"}, 400)
        item_type = body.get("item_type", "SECURITY")
        margin_category = body.get("margin_category", "intraday")
        try:
            basket = await provider_md.margin(
                instruments=instruments, item_type=item_type,
                margin_category=margin_category)
        except ProviderMarketDataError as exc:
            return _json({"error": str(exc)}, 400)
        except Exception:
            return _json({"error": "margin fetch failed"}, 502)
        from market.serialization import _to_json_value
        return _json({"status": "ok", "data": _to_json_value(basket)})

    async def _shareholdings(request: Request) -> Response:
        isin = request.query_params.get("isin", "")
        if not isin:
            return _json({"error": "isin is required"}, 400)
        try:
            sh = await provider_md.shareholdings(isin=isin)
        except ProviderMarketDataError as exc:
            return _json({"error": str(exc)}, 400)
        except Exception:
            return _json({"error": "shareholdings fetch failed"}, 502)
        from market.serialization import _to_json_value
        return _json({"status": "ok", "data": _to_json_value(sh)})

    async def _greeks(request: Request) -> Response:
        ik = request.query_params.get("instrument_key", "")
        keys = [k.strip() for k in ik.split(",") if k.strip()]
        if not keys:
            return _json({"error": "instrument_key is required"}, 400)
        try:
            snap = await provider_md.option_greeks(instrument_keys=keys)
        except ProviderMarketDataError as exc:
            return _json({"error": str(exc)}, 400)
        except Exception:
            return _json({"error": "option greeks fetch failed"}, 502)
        from market.serialization import _to_json_value
        return _json({"status": "ok", "data": _to_json_value(snap)})

    return [
        Route("/api/market/history", endpoint=_history, methods=["GET"]),
        Route("/api/options/chain", endpoint=_chain, methods=["GET"]),
        Route("/api/margin", endpoint=_margin, methods=["POST"]),
        Route("/api/shareholdings", endpoint=_shareholdings, methods=["GET"]),
        Route("/api/options/greeks", endpoint=_greeks, methods=["GET"]),
    ]


def build_admin_routes(store: Any, data_dir: Any) -> list[Route]:
    """Operational routes: database backup (contains ciphertext only)."""

    async def _backup(request: Request) -> Response:  # noqa: ARG001
        import datetime as _dt
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = data_dir / "backups" / f"events-{stamp}.db"

        def _do():
            store.backup_to(str(dest))
            return str(dest)

        try:
            path = await asyncio.to_thread(_do)
        except Exception:
            return _json({"error": "backup failed"}, 500)
        return _json({"status": "ok", "file": os.path.basename(path)})

    return [
        Route("/api/admin/backup", endpoint=_backup, methods=["POST"]),
    ]


def build_api_meta_routes() -> list[Route]:
    """GET /api — safe, self-describing capability metadata."""

    async def _meta(request: Request) -> Response:  # noqa: ARG001
        return _json({
            "service": "MarketHub",
            "capabilities": {
                "market": [
                    "GET /api/market/quotes",
                    "GET /api/market/depths",
                    "GET /api/market/quote/{exchange}/{instrument_token}",
                    "GET /api/market/depth/{exchange}/{instrument_token}",
                    "GET /api/market/stream (SSE)",
                    "GET /api/market/history"
                    "?instrument_key&unit&interval&from&to",
                ],
                "instruments": [
                    "GET /api/instruments/search?q&exchange&type&provider&limit",
                    "POST /api/instruments/sync {provider}",
                    "GET /api/instruments/sync-state",
                ],
                "watchlists": [
                    "GET|POST /api/watchlists",
                    "PATCH|DELETE /api/watchlists/{id}",
                    "POST /api/watchlists/{id}/items",
                    "DELETE /api/watchlists/{id}/items/{item_id}",
                    "POST /api/watchlists/{id}/reorder",
                    "GET /api/watchlists/export",
                    "POST /api/watchlists/import",
                ],
                "options": [
                    "GET /api/options/underlyings?q",
                    "GET /api/options/expiries?underlying",
                    "GET /api/options/chain"
                    "?instrument_key&exchange&expiry",
                    "GET /api/options/greeks?instrument_key",
                ],
                "alerts": [
                    "GET|POST /api/alerts",
                    "DELETE /api/alerts/{id}",
                    "POST /api/alerts/{id}/rearm",
                    "POST /api/alerts/{id}/enabled",
                    "GET /api/alerts/history?limit&offset&provider",
                    "DELETE /api/alerts/history",
                ],
                "broker": [
                    "POST /api/margin {instruments[],item_type,margin_category}",
                    "GET /api/shareholdings?isin",
                ],
                "sources": [
                    "GET /api/sources/status",
                    "POST /api/sources/{name}/start",
                    "POST /api/sources/{name}/stop",
                    "POST /api/sources/{name}/restart",
                ],
                "auth": [
                    "GET /api/auth/upstox/status",
                    "GET /api/auth/upstox/login",
                    "GET /auth/upstox/callback",
                    "POST /api/auth/upstox/token",
                    "GET|POST|DELETE /api/settings/upstox",
                    "GET /api/auth/fyers/login",
                    "GET /auth/fyers/callback",
                    "GET|POST|DELETE /api/settings/fyers",
                ],
                "intelligence": [
                    "GET /api/market/search?q&types&exchange&expiry",
                    "GET /api/futures?underlying&expiry",
                    "GET /api/options/chain/view?underlying&expiry&window",
                ],
                "application": [
                    "GET|POST /api/settings/app (public_base_url)",
                    "GET /api/diagnostics (support snapshot, no secrets)",
                ],
                "admin": ["POST /api/admin/backup"],
            },
        })

    return [
        Route("/api", endpoint=_meta, methods=["GET"]),
    ]


def build_fyers_auth_routes(cred_store: Any,
                            redirect_uri: str = (
        "http://localhost:7070/auth/fyers/callback"),
                            runtime_token: dict[str, str] | None = None,
                            restart_fn: Any = None) -> list[Route]:
    """Fyers credential storage + OAuth login/callback (encrypted store).

    Fyers semantics (official v3): callback carries ``auth_code`` (not
    ``code``); refresh tokens ARE supported and are stored ENCRYPTED.
    Access tokens remain runtime-memory-only after decryption.

    Args:
        cred_store: encrypted credential store.
        redirect_uri: OAuth redirect target.
        runtime_token: optional dict shared with the Fyers feed's
            ``access_token_getter`` so a successful login immediately
            unblocks the feed. When None, an internal dict is used.
        restart_fn: optional coroutine called after a successful login to
            (re)start the Fyers source through SourceManager.
    """
    import hmac as _hmac
    import secrets as _secrets
    import time as _time

    # Runtime-only access token (never persisted). Refresh token lives
    # encrypted in the credential store under provider "fyers_refresh".
    # When ``runtime_token`` is supplied it is the SAME object the feed's
    # getter closes over, so login here unblocks the running feed.
    _fyers_runtime_token: dict[str, str] = (
        runtime_token if runtime_token is not None else {"access_token": ""})
    _pending: dict[str, float] = {}
    _TTL = 600

    async def _status(request: Request) -> Response:  # noqa: ARG001
        try:
            creds = cred_store.load_fyers_credentials()
        except Exception:
            creds = None
        has_refresh = bool(await asyncio.to_thread(
            cred_store.load_fyers_refresh_token))
        store = await asyncio.to_thread(cred_store.store_status)
        return _json({
            "app_id_configured": bool(creds and creds.get("app_id")),
            "secret_configured": bool(creds and creds.get("app_secret")),
            "login_available": bool(creds),
            "access_token_active": bool(_fyers_runtime_token["access_token"]),
            "refresh_token_stored": has_refresh,
            # "key_missing"/"decrypt_failed": ciphertext exists but the
            # current master.key cannot read it — a store ERROR, distinct
            # from ordinary "not configured".
            "store_error": store.get("reason"),
        })

    async def _save(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        app_id = (body or {}).get("app_id", "")
        secret_id = (body or {}).get("secret_id", "")
        pin = (body or {}).get("pin", "")
        for label, value in (("app_id", app_id), ("secret_id", secret_id)):
            if not isinstance(value, str) or not value.strip():
                return _json({"error": f"{label} is required"}, 400)
            if len(value) > 512:
                return _json({"error": f"{label} too long"}, 400)
        try:
            cred_store.save_fyers_credentials(app_id.strip(),
                                             secret_id.strip())
            # Optional PIN (encrypted): enables refresh-token session
            # restore across restarts so daily re-login is not required.
            if isinstance(pin, str) and pin.strip():
                cred_store.save_fyers_pin(pin.strip())
        except Exception:
            return _json({"error": "failed to save fyers credentials"}, 500)
        return _json({"configured": True})

    async def _delete(request: Request) -> Response:  # noqa: ARG001
        try:
            removed = await asyncio.to_thread(
                cred_store.delete_app_credentials, "fyers")
        except Exception:
            return _json({"error": "failed to delete"}, 500)
        return _json({"removed": bool(removed)})

    async def _login(request: Request) -> Response:  # noqa: ARG001
        try:
            creds = await asyncio.to_thread(
                cred_store.load_fyers_credentials)
        except Exception:
            creds = None
        if not creds:
            return _json({"error": "fyers credentials not configured"}, 503)
        from brokers.fyers.auth import FyersAuth

        now = _time.monotonic()
        for s in [s for s, e in _pending.items() if e <= now]:
            del _pending[s]
        state = _secrets.token_urlsafe(32)
        _pending[state] = now + _TTL
        url = FyersAuth(app_id=creds["app_id"],
                        secret_id=creds["app_secret"],
                        redirect_uri=redirect_uri).login_url(state=state)
        from starlette.responses import RedirectResponse
        return RedirectResponse(url, status_code=302)

    async def _callback(request: Request) -> Response:
        from starlette.responses import RedirectResponse

        def _fail(reason):
            return RedirectResponse(
                    f"/ui/?fyers_auth={reason}#/settings",
                    status_code=302)

        code = request.query_params.get("auth_code") \
            or request.query_params.get("code")
        state = request.query_params.get("state")
        if not code or not state:
            return _fail("retry")
        matched = None
        expiry = -1.0
        for pending, exp in _pending.items():
            if _hmac.compare_digest(pending, state):
                matched, expiry = pending, exp
                break
        if matched is None:
            return _fail("retry")
        if expiry < _time.monotonic():
            del _pending[matched]
            return _fail("expired")
        del _pending[matched]

        try:
            creds = await asyncio.to_thread(
                cred_store.load_fyers_credentials)
        except Exception:
            creds = None
        if not creds:
            return _fail("retry")

        from brokers.fyers.auth import FyersAuth
        auth = FyersAuth(app_id=creds["app_id"],
                         secret_id=creds["app_secret"],
                         redirect_uri=redirect_uri)
        try:
            bundle = await auth.validate_auth_code(code.strip())
        except Exception:
            return _fail("rejected")

        # TOKEN POLICY (deliberate, documented):
        #   refresh token -> encrypted persistent storage (long-lived,
        #     officially supported by Fyers; required to regain access
        #     after restart without re-login)
        #   access token  -> RUNTIME MEMORY ONLY (short-lived; always
        #     regenerable from the refresh token via the official
        #     validate-authcode refresh grant). Never persisted.
        try:
            await asyncio.to_thread(
                cred_store.save_fyers_refresh_token, bundle["refresh_token"])
        except Exception:
            return _fail("error")
        _fyers_runtime_token["access_token"] = bundle["access_token"]
        # Operator login path complete: (re)start the Fyers feed so it picks
        # up the freshly-available token via its access_token_getter gate.
        if restart_fn is not None:
            try:
                await restart_fn()
            except Exception:
                logger.warning("fyers feed restart after login failed")
        return RedirectResponse("/ui/?fyers_auth=ok#/settings", status_code=302)

    return [
        Route("/api/settings/fyers", endpoint=_status, methods=["GET"]),
        Route("/api/settings/fyers", endpoint=_save, methods=["POST"]),
        Route("/api/settings/fyers", endpoint=_delete, methods=["DELETE"]),
        Route("/api/auth/fyers/login", endpoint=_login, methods=["GET"]),
        Route("/auth/fyers/callback", endpoint=_callback, methods=["GET"]),
    ]


def build_watchlist_portability_routes(store: Any) -> list[Route]:
    """JSON export/import of watchlists (identities only — never secrets)."""

    async def _export(request: Request) -> Response:  # noqa: ARG001
        out = []
        for wl in await asyncio.to_thread(store.list_watchlists):
            items = await asyncio.to_thread(store.list_watchlist_items,
                                            wl["id"])
            out.append({
                "name": wl["name"],
                "items": [{"exchange": it["exchange"],
                           "instrument_token": it["instrument_token"],
                           "tradingsymbol": it["tradingsymbol"]}
                          for it in items],
            })
        return _json({"version": 1, "watchlists": out})

    async def _import(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        if not isinstance(body, dict) or \
                not isinstance(body.get("watchlists"), list):
            return _json({"error": "expected {version, watchlists[]}"}, 400)
        created = skipped = 0
        for wl in body["watchlists"]:
            if not isinstance(wl, dict):
                skipped += 1
                continue
            name = wl.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 64:
                skipped += 1
                continue
            existing = await asyncio.to_thread(store.list_watchlists)
            match = next((w for w in existing if w["name"] == name.strip()),
                         None)
            wl_id = match["id"] if match else (await asyncio.to_thread(
                store.create_watchlist, name.strip()))["id"]
            for it in wl.get("items") or []:
                if not isinstance(it, dict):
                    skipped += 1
                    continue
                ex, tok, sym = (it.get("exchange"), it.get("instrument_token"),
                                it.get("tradingsymbol"))
                if not all(isinstance(v, str) and v.strip()
                           for v in (ex, tok, sym)):
                    skipped += 1
                    continue
                result = await asyncio.to_thread(
                    store.add_watchlist_item, wl_id, exchange=ex.strip(),
                    instrument_token=tok.strip(),
                    tradingsymbol=sym.strip())
                if result is not None:
                    created += 1
                else:
                    skipped += 1
        return _json({"status": "ok", "items_added": created,
                      "entries_skipped": skipped})

    return [
        Route("/api/watchlists/export", endpoint=_export, methods=["GET"]),
        Route("/api/watchlists/import", endpoint=_import, methods=["POST"]),
    ]


def build_app_settings_routes(config_path: str) -> list[Route]:
    """Application-level settings (no secrets).

    Exposes the operator-configured ``public_base_url`` used to build OAuth
    callback URLs. Editing it requires a server restart to take effect (the
    redirect URI is computed once at startup). No secrets are returned or
    accepted.
    """
    import json as _json_mod
    import tempfile

    from app.config import (
        get_public_base_url,
        load_config,
        oauth_callback_url,
        validate_config,
    )

    def _read_config() -> dict[str, Any]:
        if not os.path.isfile(config_path):
            return {}
        try:
            with open(config_path, "r", encoding="utf-8") as _f:
                return _json_mod.load(_f)
        except Exception:
            return {}

    def _write_config(cfg: dict[str, Any]) -> None:
        # Atomic write: temp file + rename so a crash never corrupts config.
        _dir = os.path.dirname(os.path.abspath(config_path))
        _fd, _tmp = tempfile.mkstemp(dir=_dir, suffix=".tmp")
        try:
            with os.fdopen(_fd, "w", encoding="utf-8") as _f:
                _json_mod.dump(cfg, _f, indent=2)
                _f.write("\n")
            os.replace(_tmp, config_path)
        finally:
            if os.path.exists(_tmp):
                try:
                    os.remove(_tmp)
                except OSError:
                    pass

    async def _get(request: Request) -> Response:  # noqa: ARG001
        _cfg = _read_config()
        _base = get_public_base_url(_cfg)
        return _json({
            "public_base_url": _base,
            "fyers_callback_url": oauth_callback_url(_base, "fyers"),
            "upstox_callback_url": oauth_callback_url(_base, "upstox"),
            "requires_restart": True,
        })

    async def _post(request: Request) -> Response:
        try:
            _body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        _base = (_body or {}).get("public_base_url")
        if not isinstance(_base, str) or not _base.strip():
            return _json({"error": "public_base_url must be a non-empty string"},
                         400)
        import urllib.parse as _urlparse
        _parts = _urlparse.urlsplit(_base.strip())
        if _parts.scheme not in ("http", "https") or not _parts.netloc:
            return _json({"error": "public_base_url must be a valid http(s) URL"},
                         400)
        # Merge onto the existing (defaulted) config so we never persist a
        # broken file just because the on-disk config omits defaulted keys.
        try:
            _cfg = load_config(config_path)
        except Exception as _exc:
            return _json({"error": "cannot read current config: {0}".format(
                _exc)}, 400)
        _cfg["public_base_url"] = _base.strip()
        # Validate the full config so we never persist a broken file.
        try:
            validate_config(_cfg)
        except Exception as _exc:
            return _json({"error": "invalid configuration: {0}".format(_exc)},
                          400)
        try:
            _write_config(_cfg)
        except Exception as _exc:
            return _json({"error": "failed to write config: {0}".format(
                type(_exc).__name__)}, 500)
        return _json({"status": "ok", "requires_restart": True,
                      "public_base_url": _base.strip()})

    return [
        Route("/api/settings/app", endpoint=_get, methods=["GET"]),
        Route("/api/settings/app", endpoint=_post, methods=["POST"]),
    ]
