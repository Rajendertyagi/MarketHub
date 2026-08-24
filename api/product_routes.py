"""
Product API routes: instrument catalog, watchlists, alerts.

Thin adapters over EventStore-backed services. No provider names, no
secrets, no trading.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def build_instrument_routes(catalog: Any) -> list[Route]:
    """Routes over the canonical instrument catalog."""

    async def _search(request: Request) -> Response:
        qp = request.query_params
        results = await asyncio.to_thread(
            catalog.search,
            q=qp.get("q") or None,
            exchange=qp.get("exchange") or None,
            instrument_type=qp.get("type") or None,
            provider=qp.get("provider") or None,
            limit=min(int(qp.get("limit", 25)), 100),
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
    ]


def build_watchlist_routes(store: Any) -> list[Route]:
    """CRUD routes over persistent watchlists."""

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
        wl_id = int(request.path_params["watchlist_id"])
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
        wl_id = int(request.path_params["watchlist_id"])
        ok = await asyncio.to_thread(store.delete_watchlist, wl_id)
        return _json({"status": "ok"} if ok else {"error": "not found"},
                     200 if ok else 404)

    async def _add_item(request: Request) -> Response:
        wl_id = int(request.path_params["watchlist_id"])
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
        return _json({"status": "ok", "item": item})

    async def _remove_item(request: Request) -> Response:
        item_id = int(request.path_params["item_id"])
        ok = await asyncio.to_thread(store.remove_watchlist_item, item_id)
        return _json({"status": "ok"} if ok else {"error": "not found"},
                     200 if ok else 404)

    async def _reorder(request: Request) -> Response:
        wl_id = int(request.path_params["watchlist_id"])
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
        alert_id = int(request.path_params["alert_id"])
        ok = await asyncio.to_thread(store.delete_alert, alert_id)
        if engine:
            engine.reload()
        return _json({"status": "ok"} if ok else {"error": "not found"},
                     200 if ok else 404)

    async def _rearm(request: Request) -> Response:
        alert_id = int(request.path_params["alert_id"])
        ok = await asyncio.to_thread(store.rearm_alert, alert_id)
        if engine:
            engine.clear_notification(alert_id)
        return _json({"status": "ok"} if ok else {"error": "not found"},
                     200 if ok else 404)

    async def _enabled(request: Request) -> Response:
        alert_id = int(request.path_params["alert_id"])
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
