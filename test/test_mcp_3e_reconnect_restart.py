#!/usr/bin/env python3
"""MCP-2B.3E — Reconnect/Restart Acceptance + Durable Delivery Proof.

Proves the finalized 42-tool MCP contract across disconnect/reconnect and
process restart through the REAL subprocess Streamable HTTP ``/mcp`` boundary
(scenarios A-P plus T/U, 41 gates).

Three deliberately distinct proof layers (reported separately in the A-AS
final report):

  1. REAL MCP BOUNDARY  — alert creation, durable replay, acknowledgement,
     checkpoint persistence, consumer re-registration, pagination, and
     reconnect/restart survival are all exercised through the live MCP
     tool surface (``helpers.mcp_client.call`` / ``call_session``) against a
     real server subprocess.

  2. IN-PROCESS PRODUCTION AlertEngine QUOTE INJECTION — market-alert
     *trigger evaluation* is driven by the production ``app.alerts.AlertEngine``
     constructed in-process against the SAME SQLite DB the server uses
     (``core.persistence.store.EventStore``), exactly the "existing test
     infrastructure" pattern from ``test_alert_trigger_events.py`` (spec §5).
     The resulting durable ``alert.triggered`` event is then replayed through
     the real MCP boundary. No temporary HTTP/test route is added.

  3. LIVE BROKER → MarketService INGRESS — intentionally UNTESTED in this
     offline acceptance phase. There is no way to push a quote into the
     running server's ``MarketService`` without a broker feed (Upstox/Fyers),
     and no broker credentials exist (spec §22). This gap is documented, not
     hidden.

No changes are made to the tool surface, alert/replay semantics, DB schema,
brokers, or WebUI.

Run:
    python test/test_mcp_3e_reconnect_restart.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timezone

# Ensure project root and test dir are importable.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import (  # noqa: E402
    streamable_http_client as streamablehttp_client,
)

from app.alerts import AlertEngine  # noqa: E402
from core.persistence.store import EventStore  # noqa: E402
from market.models import Quote  # noqa: E402

from helpers import lifecycle  # noqa: E402
from helpers.lifecycle import (  # noqa: E402
    get_server_url,
    restart_server,
    restore_environment,
    start_server,
    stop_server,
)
from helpers.mcp_client import call, call_session, wait_source_ready  # noqa: E402
from helpers.runner import R  # noqa: E402
from mcp_result import safe_teardown  # noqa: E402

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _uid(suffix: str = "") -> str:
    return f"3e-{suffix}-{int(time.time() * 1000)}"


def _db_path() -> str:
    """Absolute path to the current server's SQLite DB (same file the server uses)."""
    data_dir = getattr(lifecycle, "_server_data_dir", "") or "data_test"
    return os.path.join(_PROJECT_DIR, data_dir, "events.db")


def _seed_catalog() -> None:
    """Seed the canonical instruments catalog with RELIANCE (deterministic).

    The server's MarketIntel resolves 'RELIANCE' through the instruments
    table. On a fresh isolated data dir that table is empty, so we insert the
    canonical RELIANCE row through the SAME shared SQLite DB the server uses
    (WAL — the server's next catalog query sees the committed row). This is
    the established test pattern (see test_chat.py) and requires no broker.
    """
    store = EventStore(_db_path())
    store.replace_provider_instruments("upstox", [{
        "instrument_token": "INE002A01018",
        "exchange": "NSE",
        "tradingsymbol": "RELIANCE",
        "name": "Reliance Industries Ltd",
        "instrument_type": "EQUITY",
        "segment": "EQ",
        "isin": "INE002A01018",
        "underlying": "RELIANCE",
    }])


def _mk_quote(ltp: float, token: str = "INE002A01018",
              exchange: str = "NSE", symbol: str = "RELIANCE") -> Quote:
    """Canonical deterministic quote for market-alert evaluation."""
    return Quote(instrument_token=token, exchange=exchange,
                 tradingsymbol=symbol,
                 received_ts=datetime.now(timezone.utc), ltp=ltp)


async def _market_trigger(token: str, exchange: str, symbol: str, ltp: float) -> int:
    """In-process PRODUCTION AlertEngine against the shared SQLite DB.

    This is the deterministic market-quote injection path (spec §5). The
    engine is the same class the server composes; it reloads enabled alerts
    from the shared DB on construction. The durable ``alert.triggered`` event
    it publishes is later replayed through the real MCP boundary.
    """
    store = EventStore(_db_path())
    engine = AlertEngine(store)
    fired = await engine.evaluate(_mk_quote(ltp=ltp, token=token,
                                            exchange=exchange, symbol=symbol))
    return len(fired)


def _test_source_cfg(max_events: int = 8, interval: float = 0.25,
                     initial_delay: float = 3.0) -> dict:
    """Config overrides enabling the deterministic in-server test_source."""
    return {
        "sources": {
            "test_source": {
                "type": "test_source",
                "enabled": True,
                "interval_seconds": interval,
                "event_type": "test.source.tick",
                "max_events": max_events,
                "persistent": True,
                "initial_delay_seconds": initial_delay,
            }
        }
    }


async def _pending(consumer_id: str) -> dict:
    """Replay pending events for a consumer through the real MCP boundary."""
    resp = await call("consumer_event_pending_list", {"consumer_id": consumer_id})
    if resp.get("is_error"):
        return {"events": []}
    return resp


def _alert_triggered(events: list) -> list:
    return [e for e in events
            if isinstance(e, dict) and e.get("type") == "alert.triggered"]


async def _wait_alert_count(consumer_id: str, min_count: int,
                            timeout: float = 20.0) -> int:
    """Bounded poll until at least min_count alert.triggered events are replayable."""
    deadline = time.monotonic() + timeout
    last = 0
    while time.monotonic() < deadline:
        try:
            pending = await _pending(consumer_id)
            last = len(_alert_triggered(pending.get("events", [])))
            if last >= min_count:
                return last
        except Exception:
            pass
        await asyncio.sleep(0.25)
    return last


async def _wait_new_alert_event(consumer_id: str, known_ids: set,
                                timeout: float = 20.0) -> dict | None:
    """Bounded poll until an alert.triggered event NOT in known_ids appears.

    ``known_ids`` should be the set of alert event ids observed immediately
    after a restart, so a returned event is guaranteed to have fired AFTER the
    restart (post-restart ticks are not deduped while the bounded source still
    has ticks remaining).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            pending = await _pending(consumer_id)
            for e in _alert_triggered(pending.get("events", [])):
                if e["id"] not in known_ids:
                    return e
        except Exception:
            pass
        await asyncio.sleep(0.25)
    return None


async def _ack(consumer_id: str, event_id: str) -> dict:
    return await call("consumer_event_acknowledge",
                      {"consumer_id": consumer_id, "event_id": event_id})


async def _checkpoint(consumer_id: str) -> dict:
    return await call("consumer_checkpoint_get", {"consumer_id": consumer_id})


async def _create_generic_alert(consumer_id: str, *, one_shot: bool = False) -> dict:
    return await call("alert_create", {
        "consumer_id": consumer_id,
        "source": "test_source",
        "field_path": "tick",
        "operator": "gte",
        "value": 1,
        "one_shot": one_shot,
    })


async def _create_market_alert(instrument_query: str = "RELIANCE",
                               operator: str = "gt", threshold: float = 100.0,
                               field: str = "ltp") -> dict:
    return await call("market_alert_create", {
        "instrument_query": instrument_query,
        "operator": operator,
        "threshold": threshold,
        "field": field,
    })


def _market_identity(created: dict) -> tuple[str, str]:
    """Return (exchange, instrument_token) from a market_alert_create response."""
    inst = created.get("instrument") or {}
    key = inst.get("instrument_key") or ""
    exchange, _, token = key.partition(":")
    return exchange, token


# ---------------------------------------------------------------------------
# Scenario A — consumer survives MCP reconnect (same process)
# ---------------------------------------------------------------------------

async def scenario_a(runner: R) -> None:
    name = "A-consumer-survives-reconnect"
    proc = None
    try:
        proc = await start_server()
        cid = _uid("a")
        url = get_server_url()

        # Session S1: register + verify checkpoint.
        async with streamablehttp_client(url) as (r, w):
            async with ClientSession(r, w) as s1:
                await s1.initialize()
                reg = await call_session(s1, "consumer_register",
                                         {"consumer_id": cid})
                runner.assert_eq(name + "-register", reg.get("status"), "registered")
                cp = await call_session(s1, "consumer_checkpoint_get",
                                        {"consumer_id": cid})
                runner.assert_eq(name + "-cp-before", cp.get("checkpoint"), 0)
        # S1 closed = disconnect.

        # Reconnect with a fresh session: consumer state must survive.
        cp2 = await call("consumer_checkpoint_get", {"consumer_id": cid})
        runner.assert_eq(name + "-consumer-id", cp2.get("consumer_id"), cid)
        runner.assert_eq(name + "-cp-after", cp2.get("checkpoint"), 0)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario B — generic alert fires while AI disconnected
# ---------------------------------------------------------------------------

async def scenario_b(runner: R) -> None:
    name = "B-generic-fires-while-disconnected"
    proc = None
    try:
        proc = await start_server(_test_source_cfg(max_events=8))
        cid = _uid("b")
        url = get_server_url()

        # Session S1: register + create the generic alert, then disconnect.
        async with streamablehttp_client(url) as (r, w):
            async with ClientSession(r, w) as s1:
                await s1.initialize()
                await call_session(s1, "consumer_register", {"consumer_id": cid})
                created = await call_session(s1, "alert_create", {
                    "consumer_id": cid, "source": "test_source",
                    "field_path": "tick", "operator": "gte", "value": 1,
                    "one_shot": False})
                runner.assert_eq(name + "-created", created.get("status"), "created")
        # S1 closed = disconnected; test_source ticks fire in the server process.

        # Reconnect and verify the durable alert.triggered is replayable.
        count = await _wait_alert_count(cid, 1)
        runner.assert_ge(name + "-fired", count, 1)
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-replayed", len(evts), 1)
        ev = evts[0]
        data = ev.get("data", {})
        runner.assert_true(name + "-id", bool(ev.get("id")), "no event id")
        runner.assert_true(name + "-seq", ev.get("sequence") is not None,
                           "no sequence")
        runner.assert_eq(name + "-family", data.get("alert_family"), "generic")
        runner.assert_eq(name + "-consumer", data.get("consumer_id"), cid)
        runner.assert_eq(name + "-condition-field",
                         data.get("condition", {}).get("field"), "tick")
        runner.assert_eq(name + "-matched-type",
                         data.get("observed", {}).get("matched_event_type"),
                         "test.source.tick")
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario C — market alert fires while AI disconnected
# ---------------------------------------------------------------------------

async def scenario_c(runner: R) -> None:
    name = "C-market-fires-while-disconnected"
    proc = None
    try:
        proc = await start_server()
        cid = _uid("c")
        url = get_server_url()
        _seed_catalog()

        # Session S1: register + create the market alert, then disconnect.
        async with streamablehttp_client(url) as (r, w):
            async with ClientSession(r, w) as s1:
                await s1.initialize()
                await call_session(s1, "consumer_register", {"consumer_id": cid})
                created = await call_session(s1, "market_alert_create", {
                    "instrument_query": "RELIANCE", "operator": "gt",
                    "threshold": 100.0, "field": "ltp"})
                runner.assert_eq(name + "-created", created.get("status"), "created")
        alert_id = created["alert"]["id"]
        exchange, token = _market_identity(created)
        runner.assert_true(name + "-resolved", bool(token), "instrument not resolved")
        # S1 closed = disconnected.

        # In-process production AlertEngine quote injection (layer 2).
        fired = await _market_trigger(token=token, exchange=exchange,
                                      symbol="RELIANCE", ltp=150.0)
        runner.assert_ge(name + "-fired", fired, 1)

        # Reconnect and replay through the real MCP boundary (layer 1).
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-replayed", len(evts), 1)
        ev = evts[0]
        data = ev.get("data", {})
        runner.assert_eq(name + "-family", data.get("alert_family"), "market")
        runner.assert_eq(name + "-alert-id", data.get("alert_id"), alert_id)
        runner.assert_eq(name + "-version", data.get("version"), 1)
        runner.assert_eq(name + "-observed", data.get("observed", {}).get("value"),
                         150.0)
        runner.assert_eq(name + "-instrument-token",
                         data.get("instrument", {}).get("instrument_token"), token)
        ts = datetime.fromisoformat(data["triggered_at"])
        runner.assert_true(name + "-tz",
                           ts.tzinfo is not None and ts.utcoffset() is not None,
                           "triggered_at not timezone-aware ISO-8601")
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario D — process restart with pending event
# ---------------------------------------------------------------------------

async def scenario_d(runner: R) -> None:
    name = "D-restart-with-pending"
    proc = None
    try:
        proc = await start_server()
        cid = _uid("d")
        _seed_catalog()
        await call("consumer_register", {"consumer_id": cid})
        created = await call("market_alert_create", {
            "instrument_query": "RELIANCE", "operator": "gt",
            "threshold": 100.0, "field": "ltp"})
        exchange, token = _market_identity(created)
        await _market_trigger(token=token, exchange=exchange,
                              symbol="RELIANCE", ltp=150.0)

        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-pre-restart", len(evts), 1)
        e1 = evts[0]
        e1_id, e1_seq, e1_payload = e1["id"], e1["sequence"], e1["data"]

        proc = await restart_server()

        pending2 = await _pending(cid)
        evts2 = _alert_triggered(pending2.get("events", []))
        runner.assert_true(name + "-present",
                           any(e["id"] == e1_id for e in evts2),
                           "event lost after restart")
        e1b = next(e for e in evts2 if e["id"] == e1_id)
        runner.assert_eq(name + "-same-seq", e1b["sequence"], e1_seq)
        runner.assert_eq(name + "-same-payload", e1b["data"], e1_payload)

        ack = await _ack(cid, e1_id)
        runner.assert_ge(name + "-cp-advanced", ack.get("checkpoint", 0), e1_seq)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario E — process restart after ack
# ---------------------------------------------------------------------------

async def scenario_e(runner: R) -> None:
    name = "E-restart-after-ack"
    proc = None
    try:
        proc = await start_server()
        cid = _uid("e")
        _seed_catalog()
        await call("consumer_register", {"consumer_id": cid})
        created = await call("market_alert_create", {
            "instrument_query": "RELIANCE", "operator": "gt",
            "threshold": 100.0, "field": "ltp"})
        exchange, token = _market_identity(created)
        await _market_trigger(token=token, exchange=exchange,
                              symbol="RELIANCE", ltp=150.0)

        pending = await _pending(cid)
        e1 = _alert_triggered(pending.get("events", []))[0]
        ack = await _ack(cid, e1["id"])
        cp1 = ack.get("checkpoint", 0)
        runner.assert_ge(name + "-cp1", cp1, e1["sequence"])

        proc = await restart_server()

        cp2 = await _checkpoint(cid)
        runner.assert_eq(name + "-cp-persisted", cp2.get("checkpoint"), cp1)
        pending2 = await _pending(cid)
        ids2 = {e["id"] for e in pending2.get("events", [])}
        runner.assert_not_in(name + "-acked-absent", e1["id"], ids2)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario F — multiple offline events
# ---------------------------------------------------------------------------

async def scenario_f(runner: R) -> None:
    name = "F-multiple-offline-events"
    proc = None
    try:
        proc = await start_server(_test_source_cfg(max_events=8))
        cid = _uid("f")
        url = get_server_url()

        async with streamablehttp_client(url) as (r, w):
            async with ClientSession(r, w) as s1:
                await s1.initialize()
                await call_session(s1, "consumer_register", {"consumer_id": cid})
                await call_session(s1, "alert_create", {
                    "consumer_id": cid, "source": "test_source",
                    "field_path": "tick", "operator": "gte", "value": 1,
                    "one_shot": False})
        # Disconnected; ticks fire in the server process.

        count = await _wait_alert_count(cid, 3)
        runner.assert_ge(name + "-count", count, 3)
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-events", len(evts), 3)
        ids = [e["id"] for e in evts]
        runner.assert_eq(name + "-unique-ids", len(set(ids)), len(ids))
        seqs = [e["sequence"] for e in evts]
        runner.assert_true(name + "-strictly-increasing",
                           all(b > a for a, b in zip(seqs, seqs[1:])),
                           "sequences not strictly increasing")
        for e in evts:
            runner.assert_eq(name + "-owner", e["data"].get("consumer_id"), cid)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario G — partial ack then reconnect
# ---------------------------------------------------------------------------

async def scenario_g(runner: R) -> None:
    name = "G-partial-ack-reconnect"
    proc = None
    try:
        proc = await start_server(_test_source_cfg(max_events=8))
        cid = _uid("g")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)

        count = await _wait_alert_count(cid, 3)
        runner.assert_ge(name + "-enough", count, 3)
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        e1, e2, e3 = evts[0], evts[1], evts[2]
        await _ack(cid, e1["id"])

        # Reconnect (fresh session) — only E1 must be gone.
        pending2 = await _pending(cid)
        ids2 = {e["id"] for e in _alert_triggered(pending2.get("events", []))}
        runner.assert_not_in(name + "-e1-acked", e1["id"], ids2)
        runner.assert_in(name + "-e2-pending", e2["id"], ids2)
        runner.assert_in(name + "-e3-pending", e3["id"], ids2)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario H — redelivery without ack
# ---------------------------------------------------------------------------

async def scenario_h(runner: R) -> None:
    name = "H-redelivery-no-ack"
    proc = None
    try:
        proc = await start_server(_test_source_cfg(max_events=8))
        cid = _uid("h")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)

        count = await _wait_alert_count(cid, 1)
        runner.assert_ge(name + "-fired", count, 1)
        pending = await _pending(cid)
        e1 = _alert_triggered(pending.get("events", []))[0]

        # No ack; reconnect — the same event must be redelivered.
        pending2 = await _pending(cid)
        ids2 = {e["id"] for e in _alert_triggered(pending2.get("events", []))}
        runner.assert_in(name + "-redelivered", e1["id"], ids2)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario I — two consumers independent across restart
# ---------------------------------------------------------------------------

async def scenario_i(runner: R) -> None:
    name = "I-two-consumers-restart"
    proc = None
    try:
        proc = await start_server()
        cid_a = _uid("i-a")
        cid_b = _uid("i-b")
        _seed_catalog()
        await call("consumer_register", {"consumer_id": cid_a})
        await call("consumer_register", {"consumer_id": cid_b})
        created = await call("market_alert_create", {
            "instrument_query": "RELIANCE", "operator": "gt",
            "threshold": 100.0, "field": "ltp"})
        exchange, token = _market_identity(created)
        await _market_trigger(token=token, exchange=exchange,
                              symbol="RELIANCE", ltp=150.0)

        # Broadcast: both consumers receive the same event.
        pa = await _pending(cid_a)
        pb = await _pending(cid_b)
        ea = _alert_triggered(pa.get("events", []))
        eb = _alert_triggered(pb.get("events", []))
        runner.assert_ge(name + "-a-received", len(ea), 1)
        runner.assert_ge(name + "-b-received", len(eb), 1)
        e1 = ea[0]
        runner.assert_true(name + "-same-event",
                           any(e["id"] == e1["id"] for e in eb),
                           "broadcast event differs between consumers")

        # Ack only A.
        await _ack(cid_a, e1["id"])

        proc = await restart_server()

        pa2 = await _pending(cid_a)
        pb2 = await _pending(cid_b)
        ids_a2 = {e["id"] for e in pa2.get("events", [])}
        ids_b2 = {e["id"] for e in pb2.get("events", [])}
        runner.assert_not_in(name + "-a-acked-gone", e1["id"], ids_a2)
        runner.assert_in(name + "-b-still-pending", e1["id"], ids_b2)
        cpa = await _checkpoint(cid_a)
        cpb = await _checkpoint(cid_b)
        runner.assert_ge(name + "-a-cp-advanced", cpa.get("checkpoint", 0),
                         e1["sequence"])
        runner.assert_eq(name + "-b-cp-zero", cpb.get("checkpoint"), 0)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario J — generic owner routing across restart
# ---------------------------------------------------------------------------

async def scenario_j(runner: R) -> None:
    name = "J-owner-routing-restart"
    proc = None
    try:
        proc = await start_server(_test_source_cfg(max_events=8))
        owner = _uid("j-owner")
        other = _uid("j-other")
        await call("consumer_register", {"consumer_id": owner})
        await call("consumer_register", {"consumer_id": other})
        await _create_generic_alert(owner)

        count = await _wait_alert_count(owner, 1)
        runner.assert_ge(name + "-owner-fired", count, 1)
        po = await _pending(owner)
        pother = await _pending(other)
        eo = _alert_triggered(po.get("events", []))
        eother = _alert_triggered(pother.get("events", []))
        runner.assert_ge(name + "-owner-has", len(eo), 1)
        runner.assert_eq(name + "-other-none", len(eother), 0)
        e1 = eo[0]

        proc = await restart_server()

        po2 = await _pending(owner)
        pother2 = await _pending(other)
        eo2 = _alert_triggered(po2.get("events", []))
        eother2 = _alert_triggered(pother2.get("events", []))
        runner.assert_in(name + "-owner-still-has", e1["id"],
                         {e["id"] for e in eo2})
        runner.assert_eq(name + "-other-still-none", len(eother2), 0)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario K — pagination across reconnect
# ---------------------------------------------------------------------------

async def scenario_k(runner: R) -> None:
    name = "K-pagination-reconnect"
    proc = None
    try:
        proc = await start_server(_test_source_cfg(max_events=8))
        cid = _uid("k")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)

        count = await _wait_alert_count(cid, 4)
        runner.assert_ge(name + "-enough", count, 4)
        # Let the bounded test_source finish (max_events reached) so the
        # pending set is STABLE before snapshotting and paginating — otherwise
        # new ticks racing in during pagination make the two views diverge.
        await wait_source_ready("test_source", {"completed"}, timeout=15)
        full = await _pending(cid)
        full_ids = {e["id"] for e in full.get("events", [])}
        full_alerts = {e["id"] for e in _alert_triggered(full.get("events", []))}
        runner.assert_ge(name + "-full-count", len(full_ids), 4)

        # Paginate with limit=2; each call() is a fresh session = reconnect.
        collected: list[dict] = []
        after = None
        page = 0
        while True:
            args: dict = {"consumer_id": cid, "limit": 2}
            if after is not None:
                args["after_sequence"] = after
            resp = await call("consumer_event_pending_list", args)
            evts = resp.get("events", [])
            collected.extend(evts)
            page += 1
            runner.assert_true(name + f"-page{page}-no-overlap",
                               len({e["id"] for e in collected}) == len(collected),
                               "duplicate event across pages")
            if not resp.get("has_more") or not evts:
                break
            after = resp.get("next_after_sequence")

        runner.assert_true(name + "-complete",
                           {e["id"] for e in collected} == full_ids,
                           "pagination missed events")
        runner.assert_true(name + "-alerts-covered",
                           {e["id"] for e in _alert_triggered(collected)} == full_alerts,
                           "pagination missed alert events")
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario L — after_sequence=0 after restart
# ---------------------------------------------------------------------------

async def scenario_l(runner: R) -> None:
    name = "L-after-zero-restart"
    proc = None
    try:
        proc = await start_server(_test_source_cfg(max_events=8))
        cid = _uid("l")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)

        count = await _wait_alert_count(cid, 2)
        runner.assert_ge(name + "-enough", count, 2)
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        e1, e2 = evts[0], evts[1]
        await _ack(cid, e1["id"])

        proc = await restart_server()

        resp = await call("consumer_event_pending_list",
                          {"consumer_id": cid, "after_sequence": 0})
        ids0 = {e["id"] for e in _alert_triggered(resp.get("events", []))}
        runner.assert_in(name + "-e2-present", e2["id"], ids0)
        runner.assert_not_in(name + "-e1-acked-absent", e1["id"], ids0)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario M — idempotent ack across reconnect
# ---------------------------------------------------------------------------

async def scenario_m(runner: R) -> None:
    name = "M-idempotent-ack"
    proc = None
    try:
        proc = await start_server(_test_source_cfg(max_events=8))
        cid = _uid("m")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)

        count = await _wait_alert_count(cid, 1)
        runner.assert_ge(name + "-fired", count, 1)
        pending = await _pending(cid)
        e1 = _alert_triggered(pending.get("events", []))[0]
        ack1 = await _ack(cid, e1["id"])
        cp1 = ack1.get("checkpoint", 0)

        # Reconnect and ack the same event again — idempotent, no regress.
        ack2 = await _ack(cid, e1["id"])
        runner.assert_eq(name + "-status", ack2.get("status"), "acknowledged")
        runner.assert_eq(name + "-cp-stable", ack2.get("checkpoint"), cp1)
        pending2 = await _pending(cid)
        ids2 = {e["id"] for e in _alert_triggered(pending2.get("events", []))}
        runner.assert_not_in(name + "-e1-absent", e1["id"], ids2)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario N — re-register same consumer
# ---------------------------------------------------------------------------

async def scenario_n(runner: R) -> None:
    name = "N-reregister-consumer"
    proc = None
    try:
        proc = await start_server()
        cid = _uid("n")
        r1 = await call("consumer_register", {"consumer_id": cid})
        runner.assert_eq(name + "-first", r1.get("status"), "registered")

        proc = await restart_server()

        r2 = await call("consumer_register", {"consumer_id": cid})
        runner.assert_eq(name + "-second", r2.get("status"), "registered")
        cp = await _checkpoint(cid)
        runner.assert_eq(name + "-cp-zero", cp.get("checkpoint"), 0)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario O — market alert re-arm + restart
# ---------------------------------------------------------------------------

async def scenario_o(runner: R) -> None:
    name = "O-market-rearm-restart"
    proc = None
    try:
        proc = await start_server()
        cid = _uid("o")
        _seed_catalog()
        await call("consumer_register", {"consumer_id": cid})
        created = await call("market_alert_create", {
            "instrument_query": "RELIANCE", "operator": "gt",
            "threshold": 100.0, "field": "ltp"})
        alert_id = created["alert"]["id"]
        exchange, token = _market_identity(created)

        # First trigger -> exactly one durable event.
        await _market_trigger(token=token, exchange=exchange,
                              symbol="RELIANCE", ltp=150.0)
        pending = await _pending(cid)
        evts1 = _alert_triggered(pending.get("events", []))
        runner.assert_eq(name + "-exactly-one", len(evts1), 1)
        e1 = evts1[0]

        # Re-arm via the real MCP boundary.
        rearm = await call("market_alert_enable", {"alert_id": alert_id})
        runner.assert_eq(name + "-rearmed", rearm.get("status"), "enabled")

        # Second trigger -> a NEW distinct durable event.
        await _market_trigger(token=token, exchange=exchange,
                              symbol="RELIANCE", ltp=160.0)
        pending2 = await _pending(cid)
        evts2 = _alert_triggered(pending2.get("events", []))
        runner.assert_eq(name + "-two-events", len(evts2), 2)
        e2 = evts2[1]
        runner.assert_not_eq(name + "-distinct-id", e2["id"], e1["id"])
        runner.assert_true(name + "-seq-increased",
                           e2["sequence"] > e1["sequence"],
                           "second event sequence not greater")

        proc = await restart_server()

        pending3 = await _pending(cid)
        evts3 = _alert_triggered(pending3.get("events", []))
        ids3 = {e["id"] for e in evts3}
        runner.assert_in(name + "-e1-survives", e1["id"], ids3)
        runner.assert_in(name + "-e2-survives", e2["id"], ids3)
        seqs = [e["sequence"] for e in evts3]
        runner.assert_eq(name + "-ordered", seqs, sorted(seqs))
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario P — generic repeating alert + restart
# ---------------------------------------------------------------------------

async def scenario_p(runner: R) -> None:
    name = "P-generic-repeating-restart"
    proc = None
    try:
        proc = await start_server(_test_source_cfg(max_events=20))
        cid = _uid("p")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)

        count = await _wait_alert_count(cid, 2)
        runner.assert_ge(name + "-fired-twice", count, 2)
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        e1, e2 = evts[0], evts[1]
        runner.assert_not_eq(name + "-distinct", e1["id"], e2["id"])

        proc = await restart_server()

        # Both fired events survive the restart (unacked).
        pending2 = await _pending(cid)
        evts2 = _alert_triggered(pending2.get("events", []))
        ids2 = {e["id"] for e in evts2}
        runner.assert_in(name + "-e1-survives", e1["id"], ids2)
        runner.assert_in(name + "-e2-survives", e2["id"], ids2)

        # Evaluation resumes: a genuinely NEW alert.triggered appears after
        # restart (post-restart ticks are not deduped — the bounded source
        # still has ticks remaining).
        new_evt = await _wait_new_alert_event(cid, ids2)
        runner.assert_true(name + "-new-events", new_evt is not None,
                           "no new alert events after restart")
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario T — generic alert persistence/evaluation after restart
# ---------------------------------------------------------------------------

async def scenario_t(runner: R) -> None:
    name = "T-generic-persistence-restart"
    proc = None
    try:
        proc = await start_server(_test_source_cfg(max_events=20))
        cid = _uid("t")
        await call("consumer_register", {"consumer_id": cid})
        created = await _create_generic_alert(cid)
        alert_id = created["alert"]["alert_id"]

        count = await _wait_alert_count(cid, 1)
        runner.assert_ge(name + "-fired", count, 1)

        proc = await restart_server()

        # Alert definition survived restart and is still enabled.
        lst = await call("alert_list", {"consumer_id": cid})
        runner.assert_true(
            name + "-definition-survived",
            any(a.get("alert_id") == alert_id and a.get("enabled")
                for a in lst.get("alerts", [])),
            "alert definition lost or disabled after restart")

        # Evaluation resumes: a genuinely NEW alert.triggered appears after
        # restart, carrying the matched tick metadata.
        pending2 = await _pending(cid)
        ids2 = {e["id"] for e in _alert_triggered(pending2.get("events", []))}
        new_evt = await _wait_new_alert_event(cid, ids2)
        runner.assert_true(name + "-resumed", new_evt is not None,
                           "no new alert event after restart")
        runner.assert_eq(name + "-matched-type",
                         new_evt["data"].get("observed", {}).get("matched_event_type"),
                         "test.source.tick")
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Scenario U — market alert persistence/evaluation after restart
# ---------------------------------------------------------------------------

async def scenario_u(runner: R) -> None:
    name = "U-market-persistence-restart"
    proc = None
    try:
        proc = await start_server()
        cid = _uid("u")
        _seed_catalog()
        await call("consumer_register", {"consumer_id": cid})
        created = await call("market_alert_create", {
            "instrument_query": "RELIANCE", "operator": "gt",
            "threshold": 100.0, "field": "ltp"})
        alert_id = created["alert"]["id"]
        exchange, token = _market_identity(created)

        lst1 = await call("market_alert_list")
        runner.assert_true(
            name + "-enabled-before",
            any(a.get("id") == alert_id and a.get("enabled")
                for a in lst1.get("alerts", [])),
            "alert not enabled before restart")

        proc = await restart_server()

        # Rehydration (a): the persisted enabled alert is still listed.
        lst2 = await call("market_alert_list")
        runner.assert_true(
            name + "-enabled-after",
            any(a.get("id") == alert_id and a.get("enabled")
                for a in lst2.get("alerts", [])),
            "alert not enabled after restart (rehydration failed)")

        # Rehydration (b): a fresh in-process production AlertEngine evaluates
        # a post-restart quote and emits a durable event.
        fired = await _market_trigger(token=token, exchange=exchange,
                                      symbol="RELIANCE", ltp=150.0)
        runner.assert_ge(name + "-fired-after-restart", fired, 1)

        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-replayed", len(evts), 1)
        runner.assert_eq(name + "-family", evts[0]["data"].get("alert_family"),
                         "market")
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> int:
    runner = R()
    print("  MCP-2B.3E Reconnect/Restart Acceptance (scenarios A-P, T, U)")
    print("=" * 60)

    tests = [
        scenario_a, scenario_b, scenario_c, scenario_d, scenario_e,
        scenario_f, scenario_g, scenario_h, scenario_i, scenario_j,
        scenario_k, scenario_l, scenario_m, scenario_n, scenario_o,
        scenario_p, scenario_t, scenario_u,
    ]
    for fn in tests:
        try:
            await fn(runner)
        except Exception as exc:
            runner.fail(fn.__name__, str(exc))

    success = runner.summary()
    sys.exit(0 if success else 1)
    return success


if __name__ == "__main__":
    asyncio.run(main())