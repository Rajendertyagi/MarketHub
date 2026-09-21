"""
HTTP API route builders for MarketHub.

Owns Starlette Route objects only. Routes receive every dependency
(brokers, services) as constructor/argument injection from the application
composition root — this package never creates services, never serializes
domain models itself (delegates to market.serialization), and never reads
app.state.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from typing import Any, Callable

from sse_starlette import EventSourceResponse
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

import logging
logger = logging.getLogger("event_server")

from market.derivatives_universe import fno_underlying_rows as _fno_underlying_rows
from market.market_universe import resolve_universe as _resolve_universe
from market.scanner import ScannerEngine
from market.breadth import compute_breadth as _compute_breadth
from market.sector_heatmap import compute_sector_heatmap as _compute_sector_heatmap
from market.market_map import compute_market_map as _compute_market_map

__all__ = [
    "build_market_routes",
    "build_auth_routes",
    "build_settings_routes",
    "build_source_control_routes",
]


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def build_market_routes(
    market_broker: Any,
    market_service: Any = None,
    source_status_fn: Callable[[], list[dict]] | None = None,
    identity_resolver: Any = None,
    index_catalog: Any = None,
    subscriptions: Any = None,
    feed_provider: Any = None,
) -> list[Route]:
    """Build market API routes around injected dependencies.

    identity_resolver: optional registry resolving any known instrument
    identifier (config key, catalog token, symbol) to the MarketService
    storage key before lookup, so config keys and catalog ids address
    the same quote state.
    index_catalog: optional instrument catalog backing the canonical
    header-index list (GET /api/market/indices). Only catalog-confirmed
    major Indian indices are ever listed — never fabricated.
    """

    def _resolve_token(exchange: str, token: str) -> tuple[str, str]:
        if identity_resolver is not None:
            resolved = identity_resolver.resolve(token)
            if resolved:
                return exchange, resolved
        return exchange, token

    # -- SSE stream ----------------------------------------------------------

    async def _market_stream(request: Request) -> Response:  # noqa: ARG001
        from sse_starlette.sse import ServerSentEvent

        async def _generate():
            # Reconciliation on (re)connect: clear any stale pre-restart
            # browser state, then push the authoritative current snapshot so a
            # restarted/refreshed client never displays old values forever.
            # Live updates continue to flow after the snapshot. The reset
            # payload is a non-JSON control token so SSE parsers that only
            # surface JSON envelopes skip it while the WebUI `reset` listener
            # still fires.
            yield ServerSentEvent(data="reset", event="reset").encode()
            if market_service is not None:
                from market.serialization import quote_to_dict

                for q in await market_service.quotes():
                    envelope = json.dumps(
                        {"type": "quote", "data": quote_to_dict(q)},
                        ensure_ascii=False, allow_nan=False)
                    yield ServerSentEvent(data=envelope, event="quote").encode()

            async with market_broker.subscribe() as lines:
                async for line in lines:
                    # Broker lines are raw JSON envelopes; frame them as
                    # `quote` SSE events so the WebUI listener fires.
                    yield ServerSentEvent(data=line, event="quote").encode()

        return EventSourceResponse(
            _generate(), media_type="text/event-stream", ping=15,
        )

    # -- read-only market data -------------------------------------------------

    async def _market_quotes(request: Request) -> Response:  # noqa: ARG001
        if market_service is None:
            return _json({"error": "market service unavailable"}, 503)
        quotes = await market_service.quotes()
        from market.serialization import quote_to_dict
        return _json({"quotes": [quote_to_dict(q) for q in quotes]})

    async def _market_depths(request: Request) -> Response:  # noqa: ARG001
        if market_service is None:
            return _json({"error": "market service unavailable"}, 503)
        depths = await market_service.depths()
        from market.serialization import depth_to_dict
        return _json({"depths": [depth_to_dict(d) for d in depths]})

    async def _market_quote(request: Request) -> Response:
        if market_service is None:
            return _json({"error": "market service unavailable"}, 503)
        exchange = request.path_params.get("exchange", "")
        token = request.path_params.get("instrument_token", "")
        exchange, token = _resolve_token(exchange, token)
        q = await market_service.get_quote(exchange, token)
        if q is None:
            return _json({"error": "not found"}, 404)
        from market.serialization import quote_to_dict
        return _json(quote_to_dict(q))

    async def _market_depth(request: Request) -> Response:
        if market_service is None:
            return _json({"error": "market service unavailable"}, 503)
        exchange = request.path_params.get("exchange", "")
        token = request.path_params.get("instrument_token", "")
        exchange, token = _resolve_token(exchange, token)
        d = await market_service.get_depth(exchange, token)
        if d is None:
            return _json({"error": "not found"}, 404)
        from market.serialization import depth_to_dict
        return _json(depth_to_dict(d))

    # -- canonical header indices ---------------------------------------------

    async def _market_indices(request: Request) -> Response:  # noqa: ARG001
        from app.market_indices import resolve_indices
        if market_service is None:
            return _json({"error": "market service unavailable"}, 503)
        if index_catalog is None:
            return _json({"error": "instrument catalog unavailable"}, 503)
        resolve = identity_resolver.resolve \
            if identity_resolver is not None else None
        indices = await asyncio.to_thread(
            resolve_indices, index_catalog,
            market_service.get_quote_now, resolve)
        return _json({"indices": indices})

    # -- source / feed status ---------------------------------------------------

    async def _source_status(request: Request) -> Response:  # noqa: ARG001
        sources: list[dict] = []
        if source_status_fn is not None:
            sources = source_status_fn()
        return _json({"sources": sources})

    # -- market breadth (shared universe + canonical quote reader) -------------

    def _reader():
        # Bind the injected canonical MarketService as the quote reader.
        return market_service.get_quote_now if market_service else (lambda e, t: None)

    def _scanner_engine():
        if index_catalog is None:
            return None
        return ScannerEngine(index_catalog)

    async def _scanners_list(request: Request) -> Response:  # noqa: ARG001
        engine = _scanner_engine()
        if engine is None:
            return _json({"error": "instrument catalog unavailable"}, 503)
        return _json({"status": "ok", "scanners": engine.list_scanners()})

    async def _scanner_run(request: Request) -> Response:
        engine = _scanner_engine()
        if engine is None:
            return _json({"error": "instrument catalog unavailable"}, 503)
        if market_service is None:
            return _json({"error": "market service unavailable"}, 503)
        name = (request.path_params.get("name") or "").strip()
        universe = (request.query_params.get("universe") or "FNO").upper()
        try:
            limit = int(request.query_params.get("limit", 25))
        except ValueError:
            limit = 25
        expiry = (request.query_params.get("expiry") or None)
        try:
            atm_range = int(request.query_params.get("atm_range", 5))
        except ValueError:
            atm_range = 5
        option_type = (request.query_params.get("option_type") or "both").upper()
        try:
            result = engine.scan(
                name, universe, _reader(), limit=limit,
                expiry=expiry, atm_range=atm_range, option_type=option_type)
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)
        return _json({"status": "ok", **result.to_dict()})

    async def _breadth(request: Request) -> Response:
        if market_service is None:
            return _json({"error": "market service unavailable"}, 503)
        if index_catalog is None:
            return _json({"error": "instrument catalog unavailable"}, 503)
        universe = (request.query_params.get("universe") or "NIFTY50").upper()
        try:
            members = _resolve_universe(universe, index_catalog)
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)
        snap = _compute_breadth(
            universe, members, _reader(), as_of=None)
        return _json(snap.to_dict())

    async def _sector_heatmap(request: Request) -> Response:
        if market_service is None:
            return _json({"error": "market service unavailable"}, 503)
        if index_catalog is None:
            return _json({"error": "instrument catalog unavailable"}, 503)
        universe = (request.query_params.get("universe") or "NIFTY50").upper()
        try:
            members = _resolve_universe(universe, index_catalog)
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)
        include = (request.query_params.get("members") or "1") not in ("0", "false")
        snap = _compute_sector_heatmap(
            universe, members, _reader(), as_of=None, include_members=include,
            fno_symbols=_fno_symbols())
        return _json(snap.to_dict())

    # -- read-only diagnostics (Test Center) -----------------------------------

    async def _breadth_diag(request: Request) -> Response:  # noqa: ARG001
        if market_service is None or index_catalog is None:
            return _json({"error": "service unavailable"}, 503)
        out = {}
        for u in ("FNO", "NIFTY50"):
            try:
                members = _resolve_universe(u, index_catalog)
                snap = _compute_breadth(u, members, _reader())
                out[u] = {
                    "universe": u,
                    "eligible": snap.eligible,
                    "quoted": snap.quoted,
                    "unavailable": snap.unavailable,
                    "advances": snap.advances,
                    "declines": snap.declines,
                    "unchanged": snap.unchanged,
                    "unclassified": snap.unclassified,
                    "net_advances": snap.net_advances,
                }
            except Exception as exc:  # noqa: BLE001
                out[u] = {"error": str(exc)}
        out["reconciliation_status"] = "ok"
        return _json(out)

    async def _sector_diag(request: Request) -> Response:  # noqa: ARG001
        if market_service is None or index_catalog is None:
            return _json({"error": "service unavailable"}, 503)
        out = {}
        for u in ("FNO", "NIFTY50"):
            try:
                members = _resolve_universe(u, index_catalog)
                snap = _compute_sector_heatmap(
                    u, members, _reader())
                out[u] = {
                    "universe": u,
                    "sector_count": snap.sector_count,
                    "classified_count": snap.classified_count,
                    "unclassified_count": snap.unclassified_count,
                    "quoted": snap.quoted,
                    "advances": snap.advances,
                    "declines": snap.declines,
                    "reconciliation": snap.reconciliation,
                }
            except Exception as exc:  # noqa: BLE001
                out[u] = {"error": str(exc)}
        out["reconciliation_status"] = "ok"
        return _json(out)

    # -- market map (stock-level, sector-grouped; reuses universe + classifier) -

    def _fno_symbols() -> set[str]:
        """Uppercased F&O underlying symbols for honest workspace navigation.

        One batch catalog query (never per-symbol); empty set on any failure so
        the map still renders (tiles simply mark fno=False).
        """
        try:
            # Enumeration only — the contract counts are not needed here.
            rows = _fno_underlying_rows(
                index_catalog, provider="upstox",
                today=date.today().isoformat(), limit=2000)
            return {str(r.get("symbol", "")).upper() for r in (rows or [])}
        except Exception:  # noqa: BLE001
            return set()

    async def _market_map(request: Request) -> Response:
        if market_service is None:
            return _json({"error": "market service unavailable"}, 503)
        if index_catalog is None:
            return _json({"error": "instrument catalog unavailable"}, 503)
        universe = (request.query_params.get("universe") or "FNO").upper()
        try:
            members = _resolve_universe(universe, index_catalog)
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)
        snap = _compute_market_map(
            universe, members, _reader(), fno_symbols=_fno_symbols(), as_of=None)
        return _json(snap.to_dict())

    async def _market_map_diag(request: Request) -> Response:  # noqa: ARG001
        if market_service is None or index_catalog is None:
            return _json({"error": "service unavailable"}, 503)
        out = {}
        cross_keys = (
            "eligible_match", "quoted_cross_match", "unavailable_cross_match",
            "advances_cross_match", "declines_cross_match", "unchanged_cross_match",
        )
        ok = True
        for u in ("FNO", "NIFTY50"):
            try:
                members = _resolve_universe(u, index_catalog)
                snap = _compute_market_map(u, members, _reader())
                recon = snap.reconciliation
                if not all(recon.get(k) for k in cross_keys):
                    ok = False
                out[u] = {
                    "universe": u,
                    "eligible": snap.eligible,
                    "quoted": snap.quoted,
                    "unavailable": snap.unavailable,
                    "sector_count": len(snap.sectors),
                    "unclassified": snap.unclassified,
                    "advances": snap.advances,
                    "declines": snap.declines,
                    "unchanged": snap.unchanged,
                    "reconciliation": recon,
                }
            except Exception as exc:  # noqa: BLE001
                ok = False
                out[u] = {"error": str(exc)}
        out["reconciliation_status"] = "ok" if ok else "mismatch"
        return _json(out)

    # -- analytics cash-equity coverage (Breadth / Heatmap / Market Map) ----------
    # Shared owner: the selected analytics universe's cash-equity instruments are
    # subscribed through the EXISTING SubscriptionService reconciliation (no new
    # broker-socket path). Switching universe replaces the owner; leaving the
    # analytics views clears it. Honors the per-provider safety cap.

    ANALYTICS_CAP = 400  # bounded partial coverage for large universes (NSE_EQ)

    def _prov_of(key: str) -> str:
        if ":" in key:
            return "fyers"
        return "upstox"

    def _analytics_keys_for_universe(universe: str):
        """Resolve a universe to its cash-equity instrument keys by provider.

        Returns (members, keys_by_provider, unresolved). Raises ValueError for
        an unknown universe (surfaced as 400 by callers).
        """
        members = _resolve_universe(universe, index_catalog)
        keys_by_provider: dict[str, list[str]] = {}
        unresolved = 0
        for m in members:
            if not m.instrument_token:
                unresolved += 1
                continue
            prov = _prov_of(m.instrument_token)
            keys_by_provider.setdefault(prov, [])
            if m.instrument_token not in keys_by_provider[prov]:
                keys_by_provider[prov].append(m.instrument_token)
        return members, keys_by_provider, unresolved

    def _coverage_report(universe: str, members, keys_by_provider,
                         unresolved: int) -> dict[str, Any]:
        """Diagnostic coverage projection for a universe (no subscription change)."""
        resolved = sum(len(v) for v in keys_by_provider.values())
        quoted = unavailable = 0
        for m in members:
            if m.instrument_token is None:
                unavailable += 1
                continue
            q = _reader()(m.exchange, m.instrument_token)
            if q is not None and getattr(q, "ltp", None) is not None:
                quoted += 1
            else:
                unavailable += 1
        subscribed = 0
        feed = feed_provider("upstox") if feed_provider else None
        current = set(getattr(feed, "_instrument_keys", ())
                      or getattr(feed, "_desired", ())) if feed else set()
        for v in keys_by_provider.values():
            subscribed += sum(1 for k in v if k in current)
        provider_status = "unknown"
        if feed_provider is not None:
            provider_status = "connected" if feed_provider("upstox") is not None \
                else "unavailable"
        return {
            "universe": universe,
            "eligible": len(members),
            "resolved": resolved,
            "unresolved": unresolved,
            "desired": resolved,
            "subscribed": subscribed,
            "quoted": quoted,
            "unavailable": unavailable,
            "provider_status": provider_status,
            "as_of": None,
        }

    async def _analytics_coverage_get(request: Request) -> Response:
        if market_service is None or index_catalog is None:
            return _json({"error": "service unavailable"}, 503)
        universe = (request.query_params.get("universe") or "NIFTY50").upper()
        try:
            members, keys_by_provider, unresolved = \
                _analytics_keys_for_universe(universe)
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)
        return _json({"status": "ok",
                      **_coverage_report(universe, members, keys_by_provider,
                                         unresolved)})

    async def _analytics_coverage_post(request: Request) -> Response:
        if subscriptions is None or feed_provider is None:
            return _json({"error": "subscription service unavailable"}, 503)
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        universe = (body or {}).get("universe", "")
        if not isinstance(universe, str) or not universe.strip():
            return _json({"error": "universe is required"}, 400)
        universe = universe.strip().upper()
        if universe not in ("NIFTY50", "NIFTYNXT50", "BANKNIFTY", "FNO", "NSE_EQ"):
            return _json({"error": f"unsupported analytics universe: {universe}"},
                         400)
        try:
            members, keys_by_provider, unresolved = \
                _analytics_keys_for_universe(universe)
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)
        # Bounded safety cap: honest partial coverage for very large universes.
        capped = False
        total = sum(len(v) for v in keys_by_provider.values())
        if total > ANALYTICS_CAP:
            capped = True
            for prov in list(keys_by_provider):
                if len(keys_by_provider[prov]) > ANALYTICS_CAP:
                    keys_by_provider[prov] = keys_by_provider[prov][:ANALYTICS_CAP]
        subscriptions.set_analytics_universe(keys_by_provider)
        try:
            outcome = await subscriptions.reconcile(feed_provider)
        except Exception as exc:
            from app.subscriptions.service import SubscriptionLimitError
            if isinstance(exc, SubscriptionLimitError):
                return _json({"status": "error",
                              "error": "subscription limit exceeded",
                              "detail": str(exc)}, 400)
            logger.exception("analytics coverage apply failed")
            return _json({"error": "apply failed"}, 500)
        return _json({
            "status": "ok", "universe": universe,
            "eligible": len(members), "resolved": total,
            "unresolved": unresolved, "capped": capped,
            "by_provider": {p: len(k) for p, k in keys_by_provider.items()},
            "apply": outcome["apply"],
        })

    async def _analytics_coverage_delete(request: Request) -> Response:
        if subscriptions is None or feed_provider is None:
            return _json({"error": "subscription service unavailable"}, 503)
        subscriptions.clear_analytics_universe()
        try:
            outcome = await subscriptions.reconcile(feed_provider)
        except Exception as exc:
            logger.exception("analytics coverage clear failed")
            return _json({"error": "apply failed"}, 500)
        return _json({"status": "ok", "cleared": True, "apply": outcome["apply"]})

    return [
        Route("/api/market/stream", endpoint=_market_stream, methods=["GET"]),
        Route("/api/market/quotes", endpoint=_market_quotes, methods=["GET"]),
        Route("/api/market/indices", endpoint=_market_indices,
              methods=["GET"]),
        Route("/api/market/depths", endpoint=_market_depths, methods=["GET"]),
        Route("/api/market/quote/{exchange}/{instrument_token}",
              endpoint=_market_quote, methods=["GET"]),
        Route("/api/market/depth/{exchange}/{instrument_token}",
              endpoint=_market_depth, methods=["GET"]),
        Route("/api/market/breadth", endpoint=_breadth, methods=["GET"]),
        Route("/api/market/sector-heatmap", endpoint=_sector_heatmap,
              methods=["GET"]),
        Route("/api/market/breadth/diagnostics", endpoint=_breadth_diag,
              methods=["GET"]),
        Route("/api/market/sector-heatmap/diagnostics",
              endpoint=_sector_diag, methods=["GET"]),
        Route("/api/market/map", endpoint=_market_map, methods=["GET"]),
        Route("/api/market/map/diagnostics", endpoint=_market_map_diag,
              methods=["GET"]),
        Route("/api/market/scanners", endpoint=_scanners_list, methods=["GET"]),
        Route("/api/market/scanner/{name}", endpoint=_scanner_run,
              methods=["GET"]),
        Route("/api/market/analytics/coverage", endpoint=_analytics_coverage_get,
              methods=["GET"]),
        Route("/api/market/analytics/coverage",
              endpoint=_analytics_coverage_post, methods=["POST"]),
        Route("/api/market/analytics/coverage",
              endpoint=_analytics_coverage_delete, methods=["DELETE"]),
        Route("/api/sources/status", endpoint=_source_status, methods=["GET"]),
    ]


def build_source_control_routes(source_manager: Any) -> list[Route]:
    """Generic source lifecycle control routes (start / stop / restart).

    Routes receive the SourceManager via composition-root injection and call
    ONLY its public lifecycle operations. They never construct feeds, never
    create a MarketService or SourceManager, and never touch sockets.

    Response contract (safe reason codes only — no provider material):
        started          new background task created
        already_running  duplicate start refused; existing task untouched
        authentication_required  source declared prerequisites unmet
                                 (daily login); OAuth is NEVER auto-triggered
        unknown_source   no such registered/configured source
    """
    if source_manager is None:
        return []

    async def _source_start(request: Request) -> Response:
        name = request.path_params.get("name", "")
        result = await source_manager.start_source(name)
        if result == "started":
            return _json({"ok": True, "result": "started"})
        if result == "already_running":
            return _json({"ok": True, "result": "already_running"})
        if result == "not_ready":
            detail = source_manager.readiness_reason(name)
            return _json(
                {"ok": False, "reason": "authentication_required",
                 "detail": detail}, 409)
        return _json({"ok": False, "reason": "unknown_source"}, 404)

    async def _source_stop(request: Request) -> Response:
        name = request.path_params.get("name", "")
        was_running = source_manager.task_running(name)
        known = await source_manager.stop_source(name)
        if not known:
            return _json({"ok": False, "reason": "unknown_source"}, 404)
        # Idempotent: stopping an already-stopped source succeeds.
        # Surface the precise stop reason so the UI can distinguish an
        # operator stop from a shutdown/restart.
        status = source_manager.get_status().get(name, {})
        return _json({
            "ok": True,
            "was_running": was_running,
            "stop_reason": status.get("stop_reason"),
        })

    async def _source_restart(request: Request) -> Response:
        name = request.path_params.get("name", "")
        # Restarting a LIVE feed is always allowed. Restarting a stopped feed
        # behaves like Start and therefore honors the same readiness gate
        # (e.g. daily authentication) instead of authorizing into a 401.
        running = source_manager.task_running(name)
        if not running and source_manager.is_ready(name) is False:
            return _json(
                {"ok": False, "reason": "authentication_required"}, 409)
        ok = await source_manager.restart_source(name)
        if not ok:
            return _json({"ok": False, "reason": "unknown_source"}, 404)
        return _json({"ok": True, "result": "restarted"})

    return [
        Route("/api/sources/{name}/start", endpoint=_source_start,
              methods=["POST"]),
        Route("/api/sources/{name}/stop", endpoint=_source_stop,
              methods=["POST"]),
        Route("/api/sources/{name}/restart", endpoint=_source_restart,
              methods=["POST"]),
    ]


def build_settings_routes(
    oauth_ref: dict[str, Any],
    cred_store: Any = None,
    source_manager: Any = None,
    config_path: str | None = None,
    sources_cfg: dict[str, Any] | None = None,
) -> list[Route]:
    """Build settings routes for persistent Upstox app-credential management.

    ``oauth_ref`` is the SAME mutable dict given to build_auth_routes —
    saving here updates OAuth availability at runtime (no restart).
    ``cred_store`` is an app.secrets_store.CredentialStore (encrypted
    SQLite-backed); injected so tests can use isolated instances.

    Feed configuration routes manage the EXISTING Upstox feed source through
    the canonical config.json ``sources`` section (durable storage) and the
    runtime SourceManager. No second config system is introduced; secrets are
    never written to config.json.

    Responses NEVER contain credential values, ciphertext, or master key.
    """
    if cred_store is None:
        from app import secrets_store as _ss
        cred_store = _ss.build_default_store()

    # Default instruments so the Upstox feed factory (which requires a
    # non-empty instruments list) can register the source on enable. The
    # operator can refine instruments later; this keeps WebUI enable simple.
    # Keys MUST be Upstox-style instrument keys (Upstox rejects Fyers-style
    # symbols: every frame then fails normalization and no quotes flow).
    _DEFAULT_UPSTOX_INSTRUMENTS = [
        {"key": "NSE_INDEX|Nifty 50", "exchange": "NSE",
         "tradingsymbol": "Nifty 50"},
        {"key": "NSE_INDEX|Nifty Bank", "exchange": "NSE",
         "tradingsymbol": "Nifty Bank"},
    ]

    def _read_raw_config() -> dict[str, Any]:
        if not config_path:
            return {}
        try:
            with open(config_path, "r", encoding="utf-8") as _f:
                return json.load(_f)
        except FileNotFoundError:
            return {}
        except Exception:
            return {}

    def _write_upstox_feed(feed_cfg: dict[str, Any]) -> None:
        if not config_path:
            raise RuntimeError("config path unavailable")
        _cfg = _read_raw_config()
        if not isinstance(_cfg, dict):
            _cfg = {}
        _sources = _cfg.get("sources")
        if not isinstance(_sources, dict):
            _sources = {}
        _sources["upstox"] = feed_cfg
        _cfg["sources"] = _sources
        with open(config_path, "w", encoding="utf-8") as _f:
            json.dump(_cfg, _f, indent=2)

    def _remove_upstox_feed() -> None:
        if not config_path:
            return
        _cfg = _read_raw_config()
        if not isinstance(_cfg, dict):
            return
        _sources = _cfg.get("sources")
        if isinstance(_sources, dict) and "upstox" in _sources:
            del _sources["upstox"]
            if not _sources:
                _cfg.pop("sources", None)
            with open(config_path, "w", encoding="utf-8") as _f:
                json.dump(_cfg, _f, indent=2)

    def _feed_registered() -> bool:
        if source_manager is None:
            return False
        return "upstox" in (source_manager.enabled_sources or {})

    async def _feed_status(request: Request) -> Response:  # noqa: ARG001
        _cfg = (sources_cfg or {}).get("upstox")
        configured = isinstance(_cfg, dict)
        enabled = bool(_cfg.get("enabled")) if configured else False
        registered = _feed_registered()
        # Restart is required only when config changed but the runtime does
        # not yet have the source registered (sources are built at startup).
        restart_required = configured and not registered
        return _json({
            "configured": configured,
            "enabled": enabled,
            "type": _cfg.get("type") if configured else None,
            "instruments": _cfg.get("instruments", []) if configured else [],
            "registered": registered,
            "restart_required": restart_required,
        })

    async def _save_feed(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        enabled = bool(body.get("enabled", True))
        instruments = body.get("instruments")
        if not isinstance(instruments, list) or not instruments:
            # Reuse existing instruments when toggling, else seed defaults.
            existing = (sources_cfg or {}).get("upstox", {}).get("instruments")
            instruments = existing if isinstance(existing, list) and existing \
                else list(_DEFAULT_UPSTOX_INSTRUMENTS)

        feed_cfg = {
            "type": "upstox_feed",
            "enabled": enabled,
            "mode": "full",
            "instruments": instruments,
        }
        try:
            _write_upstox_feed(feed_cfg)
        except Exception:
            return _json({"error": "failed to persist feed configuration"}, 500)
        # Reflect in the in-memory sources dict so status is immediate
        # (the runtime registers the source on next restart).
        if sources_cfg is not None:
            sources_cfg["upstox"] = feed_cfg

        result: dict[str, Any] = {
            "configured": True,
            "enabled": enabled,
            "registered": _feed_registered(),
        }
        if enabled:
            if _feed_registered():
                # Already-registered source: restart it now so the feed comes
                # back up without a full process restart.
                try:
                    await source_manager.restart_source("upstox")
                except Exception:
                    logger.exception("upstox feed restart failed")
                result["restart_required"] = False
            else:
                # Not yet registered: a restart is required to register + start.
                result["restart_required"] = True
        elif _feed_registered():
            # Stop the running source immediately; config keeps it disabled.
            try:
                await source_manager.stop_source("upstox")
            except Exception:
                logger.exception("upstox feed stop failed")
            result["restart_required"] = False
        return _json(result)

    async def _delete_feed(request: Request) -> Response:  # noqa: ARG001
        try:
            _remove_upstox_feed()
        except Exception:
            return _json({"error": "failed to remove feed configuration"}, 500)
        if sources_cfg is not None:
            sources_cfg.pop("upstox", None)
        if _feed_registered():
            try:
                await source_manager.stop_source("upstox")
            except Exception:
                logger.exception("upstox feed stop failed")
        return _json({"configured": False, "enabled": False})

    async def _settings_status(request: Request) -> Response:  # noqa: ARG001
        status = cred_store.status()
        store = cred_store.store_status()
        # Distinguish "nothing stored" from "stored but undecryptable with
        # the current master.key" (a store ERROR, not plain unconfigured).
        status["store_error"] = store.get("reason")
        # The persisted store is the authoritative source of truth for
        # credential presence. OAuth availability must survive restart and must
        # not depend on a volatile in-memory mirror being hydrated at startup.
        status["oauth_available"] = bool(
            status.get("api_key_configured")
            and status.get("api_secret_configured")
        )
        return _json(status)

    async def _save_credentials(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        api_key = body.get("api_key")
        api_secret = body.get("api_secret")
        for label, value in (("api_key", api_key), ("api_secret", api_secret)):
            if not isinstance(value, str) or not value.strip():
                return _json({"error": f"{label} is required"}, 400)
            if len(value) > 512:
                return _json({"error": f"{label} too long"}, 400)
        try:
            cred_store.save_upstox_app_credentials(api_key.strip(),
                                                   api_secret.strip())
        except Exception:
            # Safe failure: no crypto/DB internals leaked to the client.
            return _json({"error": "failed to save credentials"}, 500)

        # Runtime reload: same dict object the auth routes hold, updated in
        # place so Login-with-Upstox becomes available without restart.
        oauth_ref["api_key"] = api_key.strip()
        oauth_ref["api_secret"] = api_secret.strip()
        return _json({"configured": True})

    async def _delete_credentials(request: Request) -> Response:  # noqa: ARG001
        try:
            removed = cred_store.delete_upstox_app_credentials()
        except Exception:
            return _json({"error": "failed to delete credentials"}, 500)
        oauth_ref["api_key"] = ""
        oauth_ref["api_secret"] = ""
        return _json({"configured": False, "removed": bool(removed)})

    return [
        Route("/api/settings/upstox", endpoint=_settings_status,
              methods=["GET"]),
        Route("/api/settings/upstox", endpoint=_save_credentials,
              methods=["POST"]),
        Route("/api/settings/upstox", endpoint=_delete_credentials,
              methods=["DELETE"]),
        Route("/api/settings/upstox/feed", endpoint=_feed_status,
              methods=["GET"]),
        Route("/api/settings/upstox/feed", endpoint=_save_feed,
              methods=["POST"]),
        Route("/api/settings/upstox/feed", endpoint=_delete_feed,
              methods=["DELETE"]),
    ]


def build_auth_routes(
    feed_ref: dict[str, Any],
    restart_fn: Callable[[], Any] | None = None,
    oauth: dict[str, Any] | None = None,
    rest: Any = None,
    cred_store: Any = None,
    sources_cfg: dict[str, Any] | None = None,
    restore_state: dict[str, Any] | None = None,
    auth_service: Any = None,
) -> list[Route]:
    """Build auth routes for runtime token management.

    Thin composition: request/receive auth input -> call AuthService /
    broker auth service -> return status/redirect. Routes own no
    persistence ordering, runtime-token ownership, restoration, expiry
    lifecycle, or feed-health auth decisions (all in app/auth/).

    ``feed_ref`` is a mutable dict holding {"feed": UpstoxFeed | None}.
    ``restart_fn`` is an async callable that stops and restarts the source.
    ``oauth`` is an optional dict {api_key, api_secret, redirect_uri} enabling
    the OAuth login/callback flow. ``rest`` is an UpstoxRest instance used for
    the code exchange (stateless transport; stores no secrets).
    ``auth_service`` optionally injects an app.auth UpstoxAuthService;
    when omitted one is built over ``cred_store`` (same behavior).

    OAuth state lives ONLY in this closure: memory-only, single-use,
    10-minute TTL. Never persisted, never logged, never returned.
    """
    import hmac
    import secrets
    import time

    _STATE_TTL_S = 600  # 10 minutes
    _pending_states: dict[str, float] = {}  # state -> monotonic expiry
    _pending_pin: dict[str, bool] = {}      # state -> PIN-mode (prompt WebUI PIN)

    # Dedicated auth subsystem owns the lifecycle; routes only delegate.
    _upstox_auth = auth_service
    if _upstox_auth is None and cred_store is not None:
        try:
            from app.auth.storage import AuthStorage as _AuthStorage
            from app.auth.upstox import UpstoxAuthService as _UpstoxAuth
            _upstox_auth = _UpstoxAuth(
                _AuthStorage(cred_store),
                feed_provider=lambda: feed_ref.get("feed"),
                restart_fn=restart_fn,
            )
        except Exception:
            _upstox_auth = None

    async def _classify_post_restart(feed: Any) -> str:
        """Classify the feed's state shortly after a credential-driven restart.

        Returns one of: "ok" (streaming/connecting/authorizing/reconnecting,
        or still transitioning after the observation window), "rejected"
        (broker rejected the token -> auth_required), "protocol" (terminal
        failure), or "stopped" (restart did not bring the feed up).
        """
        for _ in range(25):  # ~5s at 0.2s cadence
            st = feed.status().get("state")
            if st in ("streaming", "connecting", "authorizing", "reconnecting"):
                return "ok"
            if st == "auth_required":
                return "rejected"
            if st == "failed":
                return "protocol"
            await asyncio.sleep(0.2)
        if feed.status().get("state") == "stopped":
            return "stopped"
        return "ok"

    def _oauth_secrets() -> tuple[str, str]:
        """Return (api_key, api_secret) from the persisted store (authoritative).

        The encrypted CredentialStore is the single source of truth. The static
        in-memory ``oauth`` dict is only a fallback for credentials that were
        supplied via environment and never persisted.
        """
        if cred_store is not None:
            try:
                creds = cred_store.load_upstox_app_credentials()
                if creds:
                    ak = (creds.get("api_key") or "").strip()
                    sk = (creds.get("api_secret") or "").strip()
                    if ak and sk:
                        return ak, sk
            except Exception:
                logger.exception(
                    "upstox oauth: failed to load stored credentials")
        ak = (oauth.get("api_key") if isinstance(oauth, dict) else "") or ""
        sk = (oauth.get("api_secret") if isinstance(oauth, dict) else "") or ""
        return ak.strip(), sk.strip()

    def _live_oauth() -> dict[str, str]:
        """Authoritative oauth config passed to the (frozen) auth service.

        Secrets come from the persisted store; redirect_uri is non-secret
        configuration. The static ``oauth`` dict is never the source of truth.
        """
        ak, sk = _oauth_secrets()
        redirect = (oauth.get("redirect_uri") if isinstance(oauth, dict) else "") or ""
        return {
            "api_key": ak,
            "api_secret": sk,
            "redirect_uri": redirect.strip(),
        }

    def _oauth_ready() -> bool:
        if not isinstance(oauth, dict):
            return False
        redirect = (oauth.get("redirect_uri") or "").strip()
        ak, sk = _oauth_secrets()
        return bool(redirect) and bool(ak) and bool(sk)

    def _require_auth() -> Any:
        """The injected broker auth service (composition root owns it).

        Loud on misconfiguration: silently degrading to an inline lifecycle
        is exactly how auth ownership leaked into routes before.
        """
        if _upstox_auth is None:
            raise RuntimeError("upstox auth service unavailable")
        return _upstox_auth

    async def _apply_upstox_session(creds: Any) -> None:
        """Durable-session lifecycle shared by ALL Upstox login paths.

        Owned by UpstoxAuthService (persist-first, feed-failure-isolated).
        Classification afterwards is FEED diagnostics only and must never
        influence whether the login succeeded.
        """
        await _require_auth().apply_session(creds)
        feed = feed_ref.get("feed")
        if feed is not None:
            try:
                await _classify_post_restart(feed)
            except Exception:
                logger.exception("upstox login: feed restart failed")

    async def _auth_status(request: Request) -> Response:  # noqa: ARG001
        # Thin adapter: the auth service owns status projection (token=None
        # + explicit auth_state; feed state never implies auth). Request
        # parsing (oauth readiness) stays here; lifecycle lives in app/auth/.
        try:
            base = _require_auth().status(sources_cfg, restore_state)
        except Exception:
            logger.exception("upstox status failed")
            return _json({"error": "auth service unavailable",
                          "oauth_available": _oauth_ready()}, 503)
        base["oauth_available"] = _oauth_ready()
        return _json(base)

    async def _forget_session(request: Request) -> Response:  # noqa: ARG001
        """Forget the durably stored Upstox session (does NOT delete API creds).

        Thin adapter: the auth service clears runtime + durable state.
        """
        try:
            return _json(_require_auth().logout(restore_state))
        except Exception:
            logger.exception("failed to clear upstox session")
            return _json({"error": "auth service unavailable"}, 503)

    async def _oauth_login(request: Request) -> Response:  # noqa: ARG001
        if not _oauth_ready():
            return _json({"error": "oauth not configured"}, 503)

        # PIN-mode: after the redirect, store the code and prompt the operator
        # for their Upstox PIN in the WebUI instead of auto-exchanging.
        pin_mode = bool(request.query_params.get("pin"))

        # Prune expired states (memory hygiene).
        now = time.monotonic()
        expired = [s for s, exp in _pending_states.items() if exp <= now]
        for s in expired:
            del _pending_states[s]
            _pending_pin.pop(s, None)

        state = secrets.token_urlsafe(32)
        _pending_states[state] = now + _STATE_TTL_S
        _pending_pin[state] = pin_mode
        try:
            url = _require_auth().build_login_url(_live_oauth(), state)
        except Exception:
            _pending_states.pop(state, None)
            _pending_pin.pop(state, None)
            return _json({"error": "failed to build authorization URL"}, 500)
        from starlette.responses import RedirectResponse
        return RedirectResponse(url, status_code=302)

    async def _oauth_callback(request: Request) -> Response:
        from starlette.responses import RedirectResponse

        def _fail(reason: str) -> Response:
            # Reason codes only — never provider bodies, secrets, or URIs.
            return RedirectResponse(
                    f"/ui/?auth=failed&reason={reason}#/settings",
                    status_code=302)

        state = request.query_params.get("state")
        code = request.query_params.get("code")
        if not state or not code or not _oauth_ready():
            return _fail("retry")

        # Single-use + TTL + constant-time match: consume BEFORE exchange so a
        # replayed callback can never trigger a second exchange.
        matched: str | None = None
        expiry = -1.0
        for pending, exp in _pending_states.items():
            if hmac.compare_digest(pending, state):
                matched, expiry = pending, exp
                break
        if matched is None:
            return _fail("retry")   # invalid or replayed state
        if expiry < time.monotonic():
            del _pending_states[matched]
            _pending_pin.pop(matched, None)
            return _fail("expired")  # sat on the login page too long
        del _pending_states[matched]
        pin_mode = _pending_pin.pop(matched, False)

        # PIN-mode: persist the single-use code so the WebUI can complete the
        # exchange with the operator's PIN via /api/auth/upstox/pin.
        if pin_mode:
            try:
                _require_auth().stage_auth_code(code.strip())
            except Exception:
                logger.exception("oauth callback: failed to store auth code")
                return _fail("error")
            return RedirectResponse(
                "/ui/?auth=pin_required#/settings", status_code=302)

        if rest is None:
            return _fail("error")

        try:
            api_key, api_secret = _oauth_secrets()
            if not api_key or not api_secret:
                return _fail("error")
            creds = await _require_auth().exchange_code(
                rest,
                code=code.strip(),
                client_id=api_key,
                client_secret=api_secret,
                redirect_uri=oauth["redirect_uri"].strip(),
            )
        except Exception as exc:
            # Classify safely: 4xx from Upstox almost always means bad
            # credentials or a redirect-URL mismatch in the developer app.
            status = getattr(exc, "status_code", None)
            if isinstance(status, int) and 400 <= status < 500:
                return _fail("rejected")
            return _fail("network")

        # Successful exchange -> durable session. Feed (re)start failure is a
        # FEED problem and must NOT turn a valid login into auth=failed.
        await _apply_upstox_session(creds)
        return RedirectResponse("/ui/?auth=ok#/settings", status_code=302)

    async def _pin_login(request: Request) -> Response:
        """Complete a PIN-mode login: validate the stored auth code with the
        operator-supplied Upstox PIN and start the feed."""
        from starlette.responses import RedirectResponse

        def _fail(reason: str) -> Response:
            return RedirectResponse(
                    f"/ui/?auth=failed&reason={reason}#/settings",
                    status_code=302)

        if rest is None or not _oauth_ready():
            return _json({"error": "oauth not configured"}, 503)
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        pin = (body or {}).get("pin", "")
        if not isinstance(pin, str) or not pin.strip():
            return _json({"error": "pin is required"}, 400)

        # Single-use staged code (loaded + cleared by the service).
        code = _require_auth().consume_auth_code()
        if not code:
            return _json(
                {"error": "no pending Upstox login — click Login with Upstox (PIN) again"},
                400)

        try:
            api_key, api_secret = _oauth_secrets()
            if not api_key or not api_secret:
                return _json({"error": "oauth not configured"}, 503)
            creds = await _require_auth().exchange_pin(
                rest,
                code=code.strip(),
                pin=pin.strip(),
                client_id=api_key,
                client_secret=api_secret,
                redirect_uri=oauth["redirect_uri"].strip(),
            )
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if isinstance(status, int) and 400 <= status < 500:
                return _json({"error": "Upstox rejected the PIN or login"}, 400)
            return _json({"error": "could not reach Upstox during login"}, 400)

        # Successful PIN exchange -> durable session. Feed (re)start failure
        # is a FEED problem and must NOT turn a valid login into auth=failed.
        await _apply_upstox_session(creds)
        return RedirectResponse("/ui/?auth=ok#/settings", status_code=302)

    async def _submit_token(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        token = body.get("access_token", "")
        if not isinstance(token, str) or not token.strip():
            return _json({"error": "access_token is required"}, 400)
        if len(token) > 4096:
            return _json({"error": "access_token too long"}, 400)

        # Thin adapter: the service derives canonical expiry + persists.
        try:
            result = await _require_auth().submit_manual_token(token.strip())
        except Exception as exc:
            return _json({"error": f"invalid credentials: {exc}"}, 400)
        return _json(result)

    return [
        Route("/api/auth/upstox/status", endpoint=_auth_status, methods=["GET"]),
        Route("/api/auth/upstox/login", endpoint=_oauth_login, methods=["GET"]),
        Route("/auth/upstox/callback", endpoint=_oauth_callback, methods=["GET"]),
        Route("/api/auth/upstox/token", endpoint=_submit_token, methods=["POST"]),
        Route("/api/auth/upstox/pin", endpoint=_pin_login, methods=["POST"]),
        Route("/api/auth/upstox/session", endpoint=_forget_session,
              methods=["DELETE"]),
    ]
