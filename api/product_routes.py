"""
Product API routes: instrument catalog, watchlists, alerts.

Thin adapters over EventStore-backed services. No provider names, no
secrets, no trading.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from collections.abc import Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app.fyers_runtime_auth import FyersRuntimeAuth

logger = logging.getLogger(__name__)

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


def _find_source(sources_out: list, broker: str) -> dict | None:
    """Find a source snapshot by broker name/provider (read-only helper)."""
    for entry in sources_out or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "")
        provider = str(entry.get("provider") or entry.get("type") or "")
        if broker in name.lower() or broker in provider.lower():
            return entry
    return None


# Allow-listed auth-health fields per broker (read-only; secret-bearing
# keys can never pass this projection even if a status payload grew one).
_UPSTOX_DIAG_AUTH_FIELDS = frozenset({
    "auth_state", "authenticated", "login_required", "token_configured",
    "session_persisted", "session_restored", "expires_at",
    "last_auth_status",
})
_FYERS_DIAG_AUTH_FIELDS = frozenset({
    "access_token_active", "authenticated", "auth_state", "expired",
    "expiry_known", "login_required", "session_persisted",
    "session_restored", "access_token_expires_at", "last_auth_status",
})


def build_diagnostics_routes(version: str,
                             store: Any,
                             source_status_fn: Callable[[], list],
                             base_url_fn: Callable[[], str],
                             auth_status_fn: Any = None,
                             lifecycle_status_fn: Any = None) -> list[Route]:
    """GET /api/diagnostics — read-only support snapshot with NO secrets.

    Aggregates what an operator needs to report a problem: application and
    schema versions, per-source lifecycle summary, the effective public
    base URL, and a small broker auth-health section (auth facts + feed
    facts, clearly separated). Tokens, API secrets, refresh tokens, PINs,
    auth codes, authorized WSS URLs and raw broker error bodies are never
    included.

    ``lifecycle_status_fn`` reports the state of the background components
    that must have a real owner (the Fyers index poller, the previous-OI
    recorder) — so a component that is constructed but not running is
    visible rather than silent.
    """

    async def _diagnostics(request: Request) -> Response:
        import datetime as _dt

        lifecycle_out: dict[str, Any] = {}
        try:
            _lifecycle = lifecycle_status_fn() if lifecycle_status_fn else {}
            if isinstance(_lifecycle, dict):
                lifecycle_out = _lifecycle
        except Exception:
            lifecycle_out = {}
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
        # Small broker auth-health section (read-only, allow-listed keys
        # only). Feed facts merge from the source snapshot above so auth
        # and connectivity stay visually distinct in the Test Center.
        auth_out: dict[str, Any] = {}
        try:
            _auth_all = auth_status_fn() if auth_status_fn else {}
        except Exception:
            _auth_all = {}
        if isinstance(_auth_all, dict):
            _u = _auth_all.get("upstox")
            if isinstance(_u, dict):
                _usrc = _find_source(sources_out, "upstox")
                auth_out["upstox"] = {
                    k: _u.get(k) for k in _UPSTOX_DIAG_AUTH_FIELDS}
                auth_out["upstox"]["feed"] = {
                    "state": (_usrc.get("state") if _usrc else None),
                    "task_running": (_usrc.get("task_running")
                                     if _usrc else None),
                }
            _f = _auth_all.get("fyers")
            if isinstance(_f, dict):
                _fsrc = _find_source(sources_out, "fyers")
                _active = bool(_f.get("access_token_active"))
                auth_out["fyers"] = {
                    k: _f.get(k) for k in _FYERS_DIAG_AUTH_FIELDS}
                # Normalized contract: prefer the snapshot's canonical
                # auth_state/authenticated (same projection the Settings UI
                # renders, so the two can never disagree). Legacy inputs
                # without those keys fall back to the presence derivation.
                _snap_state = _f.get("auth_state")
                _snap_authed = _f.get("authenticated")
                if isinstance(_snap_authed, bool):
                    auth_out["fyers"]["authenticated"] = _snap_authed
                else:
                    auth_out["fyers"]["authenticated"] = _active
                if isinstance(_snap_state, str) and _snap_state:
                    auth_out["fyers"]["auth_state"] = _snap_state
                else:
                    auth_out["fyers"]["auth_state"] = (
                        "authenticated" if _active
                        else ("missing" if _f.get("login_required")
                              else "unknown"))
                auth_out["fyers"]["feed"] = {
                    "state": (_fsrc.get("state") if _fsrc else None),
                    "task_running": (_fsrc.get("task_running")
                                     if _fsrc else None),
                }
        return _json({
            "service": "MarketHub",
            "version": version,
            "schema_version": schema_version,
            "public_base_url": base_url,
            "sources": sources_out,
            "auth": auth_out,
            "lifecycle": lifecycle_out,
            "generated_at": _dt.datetime.now(
                _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })

    return [Route("/api/diagnostics", endpoint=_diagnostics, methods=["GET"])]


# Liquid-index symbols -> Upstox index instrument key. The catalog is
# Fyers-sourced, so it carries no Upstox keys; this mapping (the shared
# canonical index registry in app.market_indices) lets the live Upstox
# provider snapshot drive real option quotes for the indices traders
# actually use. Unknown symbols fall back to the catalog ladder (no live
# quotes) rather than fabricating data.
def _upstox_index_key(underlying: str):
    from app.market_indices import entry_for_label, label_for_underlying
    label = label_for_underlying(underlying)
    if label is None:
        return None
    entry = entry_for_label(label)
    if entry is None:
        return None
    return (entry["upstox_key"], entry["upstox_exchange"],
            entry["upstox_symbol"])


def _chain_underlying_ref(result: dict[str, Any], underlying: str,
                          provider: str | None) -> tuple[str, str, str]:
    """The provider-native reference for a chain's UNDERLYING.

    Returns ``(instrument_key, exchange, tradingsymbol)`` for the provider
    that owns the chain. ``native_key`` IS the provider's own request
    identifier (Fyers ``BSE:SENSEX-INDEX``, Upstox ``BSE_INDEX|SENSEX``)
    and is passed through unchanged: splitting on ``:`` would strip the
    exchange from a Fyers symbol, and the adapter would address the wrong
    exchange. The static index table is only the fallback for an index the
    catalogue has not confirmed.
    """
    block = result.get("underlying_instrument") or {}
    native = block.get("native_key")
    exchange = block.get("exchange") or "NSE"
    tsym = block.get("symbol") or underlying
    if native:
        return str(native), str(exchange), str(tsym)
    key = block.get("instrument_key") or ""
    if key:
        # Legacy fallback: ``instrument_key`` is "EXCHANGE:token"; the
        # provider request wants the bare key.
        if ":" in key:
            exchange, key = key.split(":", 1)
        return str(key), str(exchange), str(tsym)
    idx = _upstox_index_key(underlying)
    if idx is not None:
        return idx
    return "", exchange, tsym


async def _enrich_chain_with_provider_quotes(
        result: dict[str, Any], provider_md: Any,
        underlying: str, expiry: str | None,
        provider_ref: Callable[
            [str, str], tuple[str, str, str] | None] | None = None) -> None:
    """Merge real provider option quotes into the catalog chain rows.

    Populates each row's ``call["quote"]`` / ``put["quote"]`` with the fields
    the WebUI already renders (oi, oi_change, volume, iv, ltp, bid, ask,
    delta, gamma, theta, vega). ``rho``/``change`` are not in the snapshot, so
    they stay absent (rendered as "—", never fabricated).

    The provider is NOT hardcoded: the chain rows carry the provider that
    owns each contract, and the snapshot is requested for that provider. A
    provider's data is only ever merged into ITS OWN contracts' rows.
    """
    if not expiry:
        return
    rows = result.get("rows") or []
    if not rows:
        return
    # One provider per chain (the rows are built from the routed
    # provider's contracts); the first row names it.
    provider = None
    for row in rows:
        for side in ("call", "put"):
            leg = row.get(side) or {}
            if leg.get("provider"):
                provider = leg["provider"]
                break
        if provider:
            break
    if not provider:
        return
    block = result.get("underlying_instrument") or {}
    ref = None
    if provider_ref is not None and block.get("provider") != provider:
        try:
            ref = provider_ref(underlying, provider)
        except Exception:
            ref = None
    if ref:
        instrument_key, exchange, tradingsymbol = ref
    elif block.get("provider") and block.get("provider") != provider:
        # The underlying row belongs to ANOTHER provider and none of this
        # provider's own identifiers could be resolved. Addressing it with a
        # foreign symbol would query the wrong instrument, so enrichment is
        # skipped rather than guessed.
        logger.info("option chain enrichment skipped for %s: no %s "
                    "underlying reference", underlying, provider)
        return
    else:
        instrument_key, exchange, tradingsymbol = _chain_underlying_ref(
            result, underlying, provider)
    if not instrument_key:
        return
    try:
        snap = await provider_md.option_chain(
            instrument_key=instrument_key, exchange=exchange,
            tradingsymbol=tradingsymbol, expiry=expiry, provider=provider)
    except Exception as exc:
        logger.info("option chain enrichment skipped for %s: %s",
                    underlying, type(exc).__name__)
        return
    if snap is None:
        return
    # A provider may serve a DIFFERENT expiry than the one requested
    # (Fyers falls back to the nearest listed expiry). Merging such a
    # chain would label one expiry's quotes as another's, so the response
    # is checked, never assumed.
    served = getattr(snap, "actual_expiry", None) or getattr(
        snap, "expiry", None)
    if served and str(served)[:10] != str(expiry)[:10]:
        logger.info(
            "option chain enrichment skipped for %s: provider served "
            "expiry %s, requested %s", underlying, served, expiry)
        return
    by_key: dict[tuple[float, str], Any] = {}
    for s in getattr(snap, "strikes", []) or []:
        if s.strike is None:
            continue
        if s.call is not None:
            by_key[(s.strike, "CE")] = s.call
        if s.put is not None:
            by_key[(s.strike, "PE")] = s.put

    def to_quote(c):
        if c is None:
            return None
        return {
            "open_interest": getattr(c, "oi", None),
            "oi_change": getattr(c, "oi_change", None),
            "volume": getattr(c, "volume", None),
            "iv": getattr(c, "iv", None),
            "ltp": getattr(c, "ltp", None),
            "change": None,
            "bid": getattr(c, "bid", None),
            "ask": getattr(c, "ask", None),
            "delta": getattr(c, "delta", None),
            "gamma": getattr(c, "gamma", None),
            "theta": getattr(c, "theta", None),
            "vega": getattr(c, "vega", None),
            "rho": None,
            "provider": getattr(c, "provider", None) or provider,
        }

    for row in rows:
        strike = row.get("strike")
        for side, otype in (("call", "CE"), ("put", "PE")):
            leg = row.get(side)
            if not leg:
                continue
            # Only merge into a leg that belongs to the snapshot's
            # provider — never another provider's contract.
            leg_provider = leg.get("provider")
            if leg_provider and leg_provider != provider:
                continue
            contract = by_key.get((strike, otype))
            if contract is not None:
                leg["quote"] = to_quote(contract)

    # The catalog chain assembled before these quotes existed, so its
    # window analytics are recomputed over the rows as now returned.
    try:
        from app.market_intel import window_analytics
        result["analytics"] = window_analytics(rows)
    except Exception:
        logger.debug("window analytics recompute failed", exc_info=True)


def build_intel_routes(market_intel: Any,
                       provider_md: Any = None) -> list[Route]:
    """Unified market-intelligence routes (shared with MCP/Chat logic).

    GET /api/market/search   — structured instrument search
                               (`exact=1` = exact resolver, no substring)
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
        if qp.get("exact") in ("1", "true", "yes"):
            result = await asyncio.to_thread(
                market_intel.search_exact, q,
                exchange=qp.get("exchange") or None)
            return _json(result)
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
        # Attach real, last-session option quotes from the live provider
        # snapshot (Upstox) when available. The catalog is Fyers-sourced, so
        # we resolve the liquid-index symbol to its Upstox index key and merge
        # by (strike, option_type). Failures here never break the catalog
        # ladder — they just leave quote fields empty.
        if provider_md is not None and result.get("rows"):
            try:
                await _enrich_chain_with_provider_quotes(
                    result, provider_md, underlying, result.get("expiry"),
                    provider_ref=getattr(
                        market_intel, "provider_underlying_ref", None))
            except Exception as exc:
                logger.warning("option chain provider enrichment skipped: %s",
                               exc)
        return _json(result)

    async def _index_option_underlyings(request: Request) -> Response:
        """Canonical index option-chain underlyings (backend-owned).

        The frontend consumes this instead of hard-coding the supported
        index list. Human/canonical labels only — never raw derivative
        tokens (see /api/options/underlyings).
        """
        from app.market_indices import index_option_underlyings
        return _json({"underlyings": index_option_underlyings()})

    async def _browse(request: Request) -> Response:
        qp = request.query_params
        types = [t for t in (qp.get("types") or "").split(",") if t]
        try:
            limit = int(qp.get("limit", 50))
        except ValueError:
            return _json({"error": "limit must be an integer"}, 400)
        strike = None
        if qp.get("strike"):
            try:
                strike = float(qp["strike"])
            except ValueError:
                return _json({"error": "strike must be a number"}, 400)
        try:
            result = await asyncio.to_thread(
                market_intel.browse,
                types=types or None,
                exchange=qp.get("exchange") or None,
                segment=qp.get("segment") or None,
                underlying=qp.get("underlying") or None,
                expiry=qp.get("expiry") or None,
                option_type=qp.get("option_type") or None,
                strike=strike,
                include_expired=qp.get("include_expired") in ("1", "true"),
                cursor=qp.get("cursor") or None,
                limit=limit)
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)
        return _json(result)

    async def _pool(request: Request) -> Response:
        qp = request.query_params
        try:
            limit = int(qp.get("limit", 1000))
        except ValueError:
            return _json({"error": "limit must be an integer"}, 400)
        result = await asyncio.to_thread(market_intel.pool, limit=limit)
        return _json(result)

    return [
        Route("/api/market/search", endpoint=_search, methods=["GET"]),
        Route("/api/instruments/browse", endpoint=_browse, methods=["GET"]),
        Route("/api/instruments/pool", endpoint=_pool, methods=["GET"]),
        Route("/api/futures", endpoint=_futures, methods=["GET"]),
        Route("/api/options/chain/view", endpoint=_intel_chain,
              methods=["GET"]),
        Route("/api/options/index-underlyings",
              endpoint=_index_option_underlyings, methods=["GET"]),
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
        if not isinstance(body, dict):
            body = {}
        provider = body.get("provider", "upstox")
        masters = body.get("masters")
        if masters is not None:
            # Selective refresh: one or more FYERS master files (e.g.
            # ["BSE_FO"]) so the SENSEX leg can be refreshed without
            # re-downloading - or touching - the NSE/BSE cash masters.
            if provider != "fyers":
                return _json({"error": "masters is only supported for fyers"},
                             400)
            if not isinstance(masters, list) or not masters:
                return _json({"error": "masters must be a non-empty list"},
                             400)
            try:
                result = await asyncio.to_thread(catalog.sync_fyers,
                                                 masters=masters)
            except ValueError as exc:
                return _json({"error": str(exc)}, 400)
            except Exception as exc:
                return _json({"error": f"sync failed: "
                                       f"{type(exc).__name__}"}, 502)
            return _json({"status": "ok", **result})
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

    async def _sync_state(request: Request) -> Response:
        return _json({"providers": await asyncio.to_thread(
            catalog.sync_state)})

    async def _segments_get(request: Request) -> Response:
        """Available segments + enabled preference + catalog counts."""
        counts = await asyncio.to_thread(catalog.segment_counts)
        enabled = await asyncio.to_thread(catalog.get_enabled_segments)
        known = await asyncio.to_thread(catalog.known_segments)
        from app.instruments import DEFAULT_SEGMENTS
        segments = [{
            "segment": seg,
            "enabled": seg in enabled,
            "default": seg in DEFAULT_SEGMENTS,
            "catalog_rows": counts.get(seg, 0),
        } for seg in known]
        return _json({
            "status": "ok",
            "default": sorted(DEFAULT_SEGMENTS),
            "enabled": sorted(enabled),
            "segments": segments,
        })

    async def _segments_put(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        segments = (body or {}).get("segments")
        if not isinstance(segments, list) or not segments:
            return _json({"error": "segments (non-empty list) is required"},
                         400)
        invalid = [s for s in segments
                   if not isinstance(s, str) or not s.strip()]
        if invalid:
            return _json({"error": "invalid segment names", "items": invalid},
                         400)
        cleaned = [s.strip().upper() for s in segments]
        known = set(await asyncio.to_thread(catalog.known_segments))
        unknown = [s for s in cleaned if s not in known]
        if unknown:
            return _json({"error": "unknown segments", "items": unknown},
                         400)
        try:
            saved = await asyncio.to_thread(catalog.set_enabled_segments,
                                            cleaned)
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)
        return _json({"status": "ok", "enabled": sorted(saved)})

    return [
        Route("/api/instruments/search", endpoint=_search, methods=["GET"]),
        Route("/api/instruments/sync", endpoint=_sync, methods=["POST"]),
        Route("/api/instruments/sync-state", endpoint=_sync_state,
              methods=["GET"]),
        Route("/api/instruments/segments", endpoint=_segments_get,
              methods=["GET"]),
        Route("/api/instruments/segments", endpoint=_segments_put,
              methods=["PUT"]),
        Route("/api/options/underlyings", endpoint=_underlyings,
              methods=["GET"]),
        Route("/api/options/expiries", endpoint=_expiries, methods=["GET"]),
    ]


def build_watchlist_routes(store: Any,
                           subscription: Any = None,
                           identity_resolver: Any = None) -> list[Route]:
    """CRUD routes over persistent watchlists.

    ``subscription`` is an optional adapter exposing
    ``add(exchange, token)`` / ``remove(exchange, token)`` coroutines.
    Removal is reference-counted across ALL watchlists: the feed only
    unsubscribes when the last watchlist reference disappears.

    ``identity_resolver`` is an optional alias -> canonical-id resolver used
    to expose an additive ``canonical_id`` on each item (the stored native
    ``(exchange, instrument_token)`` remains the identity for depth/nav).
    """

    def _canonical_id(exchange: str, token: str) -> str | None:
        if identity_resolver is None:
            return None
        try:
            return identity_resolver.resolve(token)
        except Exception:
            return None

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

    async def _list(request: Request) -> Response:
        wls = await asyncio.to_thread(store.list_watchlists)
        out = []
        for wl in wls:
            wl = dict(wl)
            items = await asyncio.to_thread(
                store.list_watchlist_items, wl["id"])
            wl["items"] = [
                {**it, "canonical_id": _canonical_id(
                    it.get("exchange", ""), it.get("instrument_token", ""))}
                for it in items]
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

    async def _list(request: Request) -> Response:
        alerts = await asyncio.to_thread(store.list_market_alerts)
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
            alert = await asyncio.to_thread(store.create_market_alert, **{
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

    async def _list(request: Request) -> Response:
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

    async def _clear(request: Request) -> Response:
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
        # Explicit provider override (sent by the WebUI chart controls):
        # forwarded to the canonical service, which validates it loudly.
        # Empty/absent falls back to inference from the instrument key.
        provider = (qp.get("provider") or "").strip() or None
        try:
            candles = await provider_md.history(
                instrument_key=instrument_key,
                unit=qp.get("unit", "days"),
                interval=qp.get("interval", 1),
                from_date=qp.get("from", ""),
                to_date=qp.get("to", ""),
                provider=provider)
        except ProviderMarketDataError as exc:
            return _json({"error": str(exc)}, 400)
        except Exception:
            logger.exception("market history failed for key=%r", instrument_key)
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
            strikes = []
            for s in snap.strikes:
                strikes.append({
                    "strike": s.strike, "atm": s.atm,
                    "call": dataclasses.asdict(s.call) if s.call else None,
                    "put": dataclasses.asdict(s.put) if s.put else None,
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
        except ProviderMarketDataError as exc:
            return _json({"error": str(exc)}, 400)
        except Exception as exc:
            logger.exception("option chain provider failed")
            return _json({"error": f"option chain failed: "
                                   f"{type(exc).__name__}: {exc}"}, 500)

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
            # Retryable upstream trouble (429/5xx/network) only — client
            # errors are classified upstream. Never flatten a provider
            # outage into a fake success or hide it as a generic body.
            logger.exception("option greeks failed for keys=%r", keys)
            return _json({"error": "option greeks upstream unavailable"}, 502)
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

    async def _backup(request: Request) -> Response:
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

    async def _meta(request: Request) -> Response:
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
                    "GET /api/market/bars"
                    "?instrument_key&timeframe&from&to&limit",
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
                            runtime_auth: FyersRuntimeAuth | None = None,
                            restart_fn: Any = None,
                            restore_state: dict[str, Any] | None = None,
                            source_manager: Any = None,
                            sources_cfg: dict[str, Any] | None = None,
                            register_source_fn: Any = None,
                            start_source_fn: Any = None,
                            persist_source_fn: Any = None,
                            auth_service: Any = None,
                            apply_source_fn: Any = None) -> list[Route]:
    """Fyers credential storage + OAuth login/callback (encrypted store).

    Thin composition: request/receive auth input -> call FyersAuthService
    (app/auth/) -> return status/redirect. Routes own no persistence
    ordering, runtime-token ownership, restoration, or expiry lifecycle.

    Fyers semantics (official v3): callback carries ``auth_code`` (not
    ``code``); refresh tokens ARE supported and are stored ENCRYPTED.
    Access tokens remain runtime-memory-only after decryption.

    Args:
        cred_store: encrypted credential store.
        redirect_uri: OAuth redirect target.
        runtime_auth: the single owner of Fyers runtime auth state, shared with
            the Fyers feed's ``access_token_getter`` so a successful login
            immediately unblocks the feed. When None, an internal owner is used.
        restart_fn: optional coroutine called after a successful login to
            (re)start the Fyers source through SourceManager.
        auth_service: optional app.auth FyersAuthService (same behavior
            when omitted; constructed over cred_store + runtime_auth).
    """
    import hmac as _hmac
    import secrets as _secrets
    import time as _time

    # Thin adapter: FyersAuthService (app/auth/) owns runtime + durable
    # session lifecycle. ``runtime_auth`` is the single runtime-token owner
    # shared with the feed's getter, so login here unblocks the running
    # feed. Routes parse requests and project status; they own no lifecycle.
    _fyers_auth = auth_service
    if _fyers_auth is None and cred_store is not None:
        try:
            from app.auth.fyers import FyersAuthService as _FyersSvc
            from app.auth.storage import AuthStorage as _AuthStorage
            _fyers_auth = _FyersSvc(
                _AuthStorage(cred_store), runtime_auth,
                redirect_uri=redirect_uri)
        except Exception:
            _fyers_auth = None

    def _require_fyers() -> Any:
        if _fyers_auth is None:
            raise RuntimeError("fyers auth service unavailable")
        return _fyers_auth

    _pending: dict[str, tuple[float, str]] = {}
    _TTL = 600

    # Default post-login destination: the Fyers section of Settings —
    # never the dashboard. A caller-supplied ``next`` may override the
    # full return location, but only within the approved /ui/ surface
    # (open redirects are refused and fall back to this default).
    _DEFAULT_RETURN_TO = "/ui/#/settings/brokers"

    def _safe_return_to(value: Any) -> str:
        """Validate an operator-supplied return location (anti-open-redirect).

        Only internal ``/ui`` locations are allowed. Anything else —
        absolute URLs, protocol-relative URLs, backslashes, control
        characters, over-long values — falls back to the default.
        """
        if not isinstance(value, str) or not value:
            return _DEFAULT_RETURN_TO
        candidate = value.strip()
        if len(candidate) > 512:
            return _DEFAULT_RETURN_TO
        if not (candidate == "/ui" or candidate.startswith("/ui/") or
                candidate.startswith("/ui?") or candidate.startswith("/ui#")):
            return _DEFAULT_RETURN_TO
        lowered = candidate.lower()
        if ("://" in lowered or candidate.startswith("//")
                or "\\" in candidate
                or any(ord(c) < 32 or ord(c) == 127 for c in candidate)):
            return _DEFAULT_RETURN_TO
        return candidate

    def _redirect_with_result(return_to: str, result: str) -> Response:
        """Return to the initiating location with a safe result flag.

        Only the ``fyers_auth`` result token is appended (ok / rejected /
        retry / expired / error) — never tokens, secrets, codes, or raw
        exception detail.
        """
        from starlette.responses import RedirectResponse
        base, sep, frag = return_to.partition("#")
        joiner = "&" if "?" in base else "?"
        target = f"{base}{joiner}fyers_auth={result}"
        if frag:
            target = f"{target}#{frag}"
        return RedirectResponse(target, status_code=302)

    async def _status(request: Request) -> Response:
        # Thin adapter: auth facts from the service; source/feed lifecycle
        # state (separate concept) from the SourceManager/config.
        try:
            view = await asyncio.to_thread(
                _require_fyers().status_snapshot, restore_state)
        except Exception:
            logger.exception("fyers status failed")
            return _json({"error": "auth service unavailable"}, 503)
        _fy_cfg = (sources_cfg or {}).get("fyers")
        configured = isinstance(_fy_cfg, dict)
        enabled = bool(_fy_cfg.get("enabled")) if configured else False
        registered = False
        source_state = None
        task_running = None
        if source_manager is not None:
            registered = "fyers" in (source_manager.enabled_sources or {})
            _st = source_manager.get_status().get("fyers")
            if isinstance(_st, dict):
                source_state = _st.get("state")
                task_running = _st.get("task_running")
        view.update({
            # Source/feed lifecycle state (separate concept from auth).
            "source_configured": configured,
            "source_enabled": enabled,
            "source_registered": registered,
            "source_state": source_state,
            "task_running": task_running,
            "restart_required": configured and not registered,
        })
        return _json(view)

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
        # Thin adapter: the service owns the credential write order.
        try:
            result = await asyncio.to_thread(
                _require_fyers().save_credentials,
                app_id.strip(), secret_id.strip(),
                pin.strip() if isinstance(pin, str) else None)
        except Exception:
            return _json({"error": "failed to save fyers credentials"}, 500)
        return _json(result)

    async def _delete(request: Request) -> Response:
        try:
            removed = await asyncio.to_thread(
                cred_store.delete_app_credentials, "fyers")
        except Exception:
            return _json({"error": "failed to delete"}, 500)
        return _json({"removed": bool(removed)})

    async def _login(request: Request) -> Response:
        now = _time.monotonic()
        for s in [s for s, e in _pending.items() if e[0] <= now]:
            del _pending[s]
        state = _secrets.token_urlsafe(32)
        # Preserve the initiating Fyers settings/auth location across the
        # broker round-trip (safe, internal-only; validated above).
        _pending[state] = (
            now + _TTL,
            _safe_return_to(request.query_params.get("next")),
        )
        # Thin adapter: the service builds the login URL from stored creds.
        try:
            url = await asyncio.to_thread(
                _require_fyers().build_login_url, state)
        except Exception:
            _pending.pop(state, None)
            return _json({"error": "fyers credentials not configured"}, 503)
        from starlette.responses import RedirectResponse
        return RedirectResponse(url, status_code=302)

    async def _callback(request: Request) -> Response:
        def _fail(reason, return_to=_DEFAULT_RETURN_TO):
            return _redirect_with_result(return_to, reason)

        code = request.query_params.get("auth_code") \
            or request.query_params.get("code")
        state = request.query_params.get("state")
        if not code or not state:
            return _fail("retry")
        matched = None
        expiry = -1.0
        return_to = _DEFAULT_RETURN_TO
        for pending, (exp, saved_next) in _pending.items():
            if _hmac.compare_digest(pending, state):
                matched, expiry, return_to = pending, exp, saved_next
                break
        if matched is None:
            return _fail("retry")
        if expiry < _time.monotonic():
            del _pending[matched]
            return _fail("expired", return_to)
        del _pending[matched]

        def _fail_here(reason):
            return _fail(reason, return_to)

        # Thin adapter: exchange + persist owned by FyersAuthService.
        # TOKEN POLICY (deliberate, documented):
        #   refresh token -> encrypted persistent storage (long-lived)
        #   access token  -> encrypted cache + runtime memory (short-lived)
        try:
            fyers_auth = _require_fyers()
        except Exception:
            return _fail_here("retry")
        try:
            bundle = await fyers_auth.exchange_auth_code(code.strip())
        except Exception:
            return _fail_here("rejected")
        try:
            result = await fyers_auth.persist_login(bundle, restart_fn)
        except Exception:
            logger.warning("fyers feed login persist failed")
            return _fail_here("error")
        if not result.get("ok"):
            return _fail_here("error")
        # Success returns to the initiating Fyers settings/auth location —
        # never the dashboard — with a safe result flag for the UI banner.
        return _redirect_with_result(return_to, "ok")

    async def _forget_session(request: Request) -> Response:
        """Forget saved Fyers SESSION material (token/PIN/refresh).

        Keeps the App ID/Secret credentials so the operator does not have to
        re-enter them; only the restart-recovery session is wiped. The runtime
        token is dropped so the feed gates on a fresh login.
        """
        try:
            result = await asyncio.to_thread(
                _require_fyers().logout, restore_state)
            return _json(result)
        except Exception:
            return _json({"error": "failed to forget session"}, 500)

    async def _feed_status(request: Request) -> Response:
        _fy_cfg = (sources_cfg or {}).get("fyers")
        configured = isinstance(_fy_cfg, dict)
        registered = False
        source_state = None
        task_running = None
        if source_manager is not None:
            registered = "fyers" in (source_manager.enabled_sources or {})
            _st = source_manager.get_status().get("fyers")
            if isinstance(_st, dict):
                source_state = _st.get("state")
                task_running = _st.get("task_running")
        tbt_symbols = _fy_cfg.get("tbt_symbols") if configured else None
        if not isinstance(tbt_symbols, list):
            tbt_symbols = []
        tbt_symbols = [s for s in tbt_symbols if isinstance(s, str) and s.strip()]
        return _json({
            "configured": configured,
            "enabled": bool(_fy_cfg.get("enabled")) if configured else False,
            "registered": registered,
            "state": source_state,
            "task_running": task_running,
            "restart_required": configured and not registered,
            "tbt_enabled": bool(_fy_cfg.get("tbt_enabled")) if configured else False,
            "tbt_symbols": tbt_symbols,
        })

    async def _save_feed(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        if not isinstance(body, dict):
            return _json({"error": "invalid JSON body"}, 400)

        existing_cfg = (sources_cfg or {}).get("fyers") if isinstance((sources_cfg or {}).get("fyers"), dict) else {}

        # Strict boolean validation when provided
        if "enabled" in body and not isinstance(body.get("enabled"), bool):
            return _json({"error": "enabled must be a boolean"}, 400)
        if "tbt_enabled" in body and not isinstance(body.get("tbt_enabled"), bool):
            return _json({"error": "tbt_enabled must be a boolean"}, 400)

        enabled = body.get("enabled") if isinstance(body.get("enabled"), bool) else bool(existing_cfg.get("enabled", True))

        instruments = body.get("instruments")
        if not isinstance(instruments, list) or not instruments:
            existing_instruments = existing_cfg.get("instruments")
            instruments = existing_instruments if isinstance(existing_instruments, list) and existing_instruments \
                else [
                    {"key": "NSE:NIFTY50-INDEX", "exchange": "NSE",
                     "tradingsymbol": "NIFTY 50"},
                    {"key": "NSE:BANKNIFTY-INDEX", "exchange": "NSE",
                     "tradingsymbol": "BANKNIFTY"},
                ]

        tbt_enabled = body.get("tbt_enabled") if isinstance(body.get("tbt_enabled"), bool) else bool(existing_cfg.get("tbt_enabled", False))
        raw_tbt_symbols = body.get("tbt_symbols", existing_cfg.get("tbt_symbols", []))
        if raw_tbt_symbols is None:
            raw_tbt_symbols = []
        if isinstance(raw_tbt_symbols, str):
            # Accept comma/newline separated input for convenience.
            parts = []
            for chunk in raw_tbt_symbols.replace("\r", "\n").replace(",", "\n").split("\n"):
                chunk = chunk.strip()
                if chunk:
                    parts.append(chunk)
            raw_tbt_symbols = parts
        if not isinstance(raw_tbt_symbols, list):
            return _json({"error": "tbt_symbols must be a list of strings"}, 400)
        tbt_symbols: list[str] = []
        seen: set[str] = set()
        for item in raw_tbt_symbols:
            if not isinstance(item, str):
                return _json({"error": "tbt_symbols must be a list of strings"}, 400)
            sym = item.strip()
            if not sym or sym in seen:
                continue
            if len(sym) > 128:
                return _json({"error": "tbt_symbols entries too long"}, 400)
            seen.add(sym)
            tbt_symbols.append(sym)

        feed_cfg = {
            "type": "fyers_feed",
            "enabled": enabled,
            "mode": "full",
            "instruments": instruments,
            "tbt_enabled": tbt_enabled,
            "tbt_symbols": tbt_symbols,
        }

        persisted = False
        persist_error = None
        if sources_cfg is not None:
            sources_cfg["fyers"] = feed_cfg
        if persist_source_fn is not None:
            try:
                persist_result = persist_source_fn(feed_cfg)
                persisted = bool(persist_result) if isinstance(persist_result, bool) else True
                if not persisted:
                    persist_error = "persist_failed"
            except Exception as exc:
                persisted = False
                persist_error = type(exc).__name__
                logger.warning("failed to persist fyers source config")
        else:
            # No persistence callback: in-memory application only, not durable.
            persisted = False

        runtime_applied = False
        registered = False
        task_running = False
        runtime_error = None
        started = False
        restarted = False
        start_result = None

        if apply_source_fn is not None:
            try:
                apply_result = await apply_source_fn(feed_cfg, start_if_enabled=enabled)
                if isinstance(apply_result, dict):
                    runtime_applied = bool(apply_result.get("runtime_applied"))
                    registered = bool(apply_result.get("registered"))
                    task_running = bool(apply_result.get("task_running"))
                    runtime_error = apply_result.get("error")
                    started = bool(apply_result.get("started"))
                    restarted = bool(apply_result.get("restarted"))
                    start_result = apply_result.get("start_result")
                else:
                    runtime_applied = bool(apply_result)
                    registered = "fyers" in (source_manager.enabled_sources or {}) if source_manager is not None else False
                    task_running = bool(source_manager.task_running("fyers")) if source_manager is not None else False
            except Exception as exc:
                runtime_error = f"apply_failed:{type(exc).__name__}"
                logger.exception("fyers feed runtime apply failed")
                registered = "fyers" in (source_manager.enabled_sources or {}) if source_manager is not None else False
                try:
                    task_running = bool(source_manager.task_running("fyers")) if source_manager is not None else False
                except Exception:
                    task_running = False
        else:
            # Fallback (legacy): best-effort start/restart/stop without guaranteed reconfig
            try:
                if enabled:
                    running = False
                    if source_manager is not None:
                        try:
                            running = bool(source_manager.task_running("fyers"))
                        except Exception:
                            running = False
                    if running and source_manager is not None:
                        await source_manager.restart_source("fyers")
                        restarted = True
                        runtime_applied = True
                    elif start_source_fn is not None:
                        await start_source_fn()
                        started = True
                        runtime_applied = True
                    else:
                        runtime_applied = False
                else:
                    if source_manager is not None and "fyers" in (source_manager.enabled_sources or {}):
                        await source_manager.stop_source("fyers")
                    runtime_applied = True
            except Exception as exc:
                runtime_error = f"runtime_failed:{type(exc).__name__}"
            registered = "fyers" in (source_manager.enabled_sources or {}) if source_manager is not None else False
            try:
                task_running = bool(source_manager.task_running("fyers")) if source_manager is not None else False
            except Exception:
                task_running = False

        result: dict[str, Any] = {
            "configured": True,
            "enabled": enabled,
            "tbt_enabled": tbt_enabled,
            "tbt_symbols": tbt_symbols,
            "persisted": persisted,
            "runtime_applied": runtime_applied,
            "registered": registered,
            "task_running": task_running,
            "started": started,
            "restarted": restarted,
        }
        if start_result is not None:
            result["start_result"] = start_result
        if persist_error:
            result["persist_error"] = persist_error
        if runtime_error:
            result["runtime_error"] = runtime_error
            result["error"] = "runtime_apply_failed"
        # Back-compat hint: restart_required only when not registered/applied as expected
        if enabled:
            result["restart_required"] = not (registered and runtime_applied and runtime_error is None)
        else:
            result["restart_required"] = False
        return _json(result)

    async def _delete_feed(request: Request) -> Response:
        if source_manager is not None and \
                "fyers" in (source_manager.enabled_sources or {}):
            try:
                await source_manager.stop_source("fyers")
            except Exception:
                logger.exception("fyers feed stop failed")
        if sources_cfg is not None:
            sources_cfg.pop("fyers", None)
        if persist_source_fn is not None:
            try:
                persist_source_fn(None)
            except Exception:
                logger.warning("failed to remove fyers source config")
        return _json({"configured": False, "enabled": False})

    return [
        Route("/api/settings/fyers", endpoint=_status, methods=["GET"]),
        Route("/api/settings/fyers", endpoint=_save, methods=["POST"]),
        Route("/api/settings/fyers", endpoint=_delete, methods=["DELETE"]),
        Route("/api/settings/fyers/feed", endpoint=_feed_status,
              methods=["GET"]),
        Route("/api/settings/fyers/feed", endpoint=_save_feed,
              methods=["POST"]),
        Route("/api/settings/fyers/feed", endpoint=_delete_feed,
              methods=["DELETE"]),
        Route("/api/auth/fyers/login", endpoint=_login, methods=["GET"]),
        Route("/auth/fyers/callback", endpoint=_callback, methods=["GET"]),
        Route("/api/auth/fyers/session", endpoint=_forget_session,
              methods=["DELETE"]),
    ]


def build_watchlist_portability_routes(store: Any) -> list[Route]:
    """JSON export/import of watchlists (identities only — never secrets)."""

    async def _export(request: Request) -> Response:
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
            with open(config_path, encoding="utf-8") as _f:
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

    async def _get(request: Request) -> Response:
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
            return _json({"error": f"cannot read current config: {_exc}"}, 400)
        _cfg["public_base_url"] = _base.strip()
        # Validate the full config so we never persist a broken file.
        try:
            validate_config(_cfg)
        except Exception as _exc:
            return _json({"error": f"invalid configuration: {_exc}"},
                          400)
        try:
            _write_config(_cfg)
        except Exception as _exc:
            return _json({"error": f"failed to write config: {type(_exc).__name__}"}, 500)
        return _json({"status": "ok", "requires_restart": True,
                      "public_base_url": _base.strip()})

    return [
        Route("/api/settings/app", endpoint=_get, methods=["GET"]),
        Route("/api/settings/app", endpoint=_post, methods=["POST"]),
    ]
