#!/usr/bin/env python3
"""MCP-2B.3C replay/consumer contract cleanup regression tests.

Covers the canonical replay contract after consumer_event_list was removed
from the public MCP surface:

  * At-least-once semantics — replay does NOT acknowledge, does NOT advance
    the checkpoint; the same events are returned on every replay until acked
  * Checkpoint monotonicity — ack advances the checkpoint monotonically
  * Multi-consumer independence — separate checkpoints/acks per consumer
  * after_sequence=0 — reproduces the old from-beginning replay behavior
  * Pagination — returned / has_more / next_after_sequence contract
  * consumer_event_pending_list canonical shape
  * consumer_event_list removed from the registered surface
  * Market alert error boundaries — shared domain exceptions, no
    success-shaped {"error": ...} dicts
  * Consumer/replay error boundaries — ConsumerNotFoundError,
    EventNotFoundError, EventNotRelevantError, ValidationError

NO LIVE BROKER. Synthetic store + deterministic MarketIntel fixture only.

Run:
    python test/test_replay_contract.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from typing import Any

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import events  # noqa: E402
from core.errors import (  # noqa: E402
    AlertNotFoundError,
    ConsumerNotFoundError,
    EventNotFoundError,
    EventNotRelevantError,
    StorageError,
    ValidationError,
)
from core.persistence.store import EventStore  # noqa: E402
from helpers.runner import R  # noqa: E402


# ── Shared fixtures ───────────────────────────────────────────────────────────

class _FakeMCP:
    """Minimal MCP server mock that captures registered tools."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, name=None, **kw):
        def deco(fn):
            self.tools[name] = fn
            return fn
        return deco


class _Services:
    """Minimal services namespace backed by a real EventStore."""

    def __init__(self, store: EventStore) -> None:
        self.store = store
        self.timeouts = {"database_seconds": 5}
        self.replay_cfg = {"max_limit": 500, "default_limit": 50}
        self.market_intel = None
        self.alert_engine = None


class _StubBus:
    """Minimal subscription bus: records the last notification, does nothing else."""

    def __init__(self) -> None:
        self.last = None

    async def publish(self, item: object) -> None:
        self.last = item


class _FakeIntel:
    """Deterministic MarketIntel fixture for market-alert resolution."""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = results

    def search(self, q, limit=5):
        return {"count": len(self._results), "results": self._results}


_DEFAULT_INTEL_RESULT = {
    "instrument_key": "NSE:RELIANCE",
    "symbol": "RELIANCE",
    "exchange": "NSE",
    "instrument_token": "RELIANCE",
}


def _mk_store():
    """Return (tmp, store). Caller must keep tmp alive for the store's lifetime."""
    tmp = tempfile.TemporaryDirectory()
    return tmp, EventStore(os.path.join(tmp.name, "t.db"))


def _mk_consumer_env():
    """Real store + registered consumer + replay tools."""
    tmp, store = _mk_store()
    fake = _FakeMCP()
    services = _Services(store)
    from mcp_server.tools.consumers import register_consumer_tools
    from mcp_server.tools.replay import register_replay_tools
    register_consumer_tools(fake, services)
    register_replay_tools(fake, services)
    return tmp, store, fake, services


def _mk_market_env(results: list[dict[str, Any]] | None = None):
    """Real store + registered market-alert tools + deterministic intel."""
    tmp, store = _mk_store()
    fake = _FakeMCP()
    services = _Services(store)
    services.market_intel = _FakeIntel(
        results if results is not None else [_DEFAULT_INTEL_RESULT])
    from mcp_server.tools.market_alerts import register_market_alert_tools
    register_market_alert_tools(fake, services)
    return tmp, store, fake, services


async def _publish(store, bus, event_type: str, routing=None) -> dict:
    """Publish a persistent event directly through the canonical path."""
    return await events.publish_event(
        event_type=event_type,
        source="test",
        data={},
        persistent=True,
        routing=routing,
        store=store,
        bus=bus,
    )


def _raises(runner: R, name: str, exc_type, fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
    except exc_type:
        runner.ok(name)
        return True
    except Exception as exc:  # noqa: BLE001
        runner.fail(name, f"expected {exc_type.__name__}, got "
                          f"{type(exc).__name__}: {exc}")
        return False
    runner.fail(name, f"expected {exc_type.__name__}, no exception raised")
    return False


async def _raises_async(runner: R, name: str, exc_type, fn, *args, **kwargs) -> bool:
    try:
        await fn(*args, **kwargs)
    except exc_type:
        runner.ok(name)
        return True
    except Exception as exc:  # noqa: BLE001
        runner.fail(name, f"expected {exc_type.__name__}, got "
                          f"{type(exc).__name__}: {exc}")
        return False
    runner.fail(name, f"expected {exc_type.__name__}, no exception raised")
    return False


# ── consumer_event_list removal ───────────────────────────────────────────────

async def test_consumer_event_list_removed(runner: R) -> None:
    """RC-REMOVED: consumer_event_list is gone; pending_list is canonical."""
    _tmp, _store, fake, _services = _mk_consumer_env()
    runner.assert_true("RC-REMOVED-not-registered",
                       "consumer_event_list" not in fake.tools)
    runner.assert_true("RC-REMOVED-pending-registered",
                       "consumer_event_pending_list" in fake.tools)
    runner.assert_true("RC-REMOVED-ack-registered",
                       "consumer_event_acknowledge" in fake.tools)
    runner.assert_true("RC-REMOVED-cp-registered",
                       "consumer_checkpoint_get" in fake.tools)


# ── At-least-once semantics ───────────────────────────────────────────────────

async def test_replay_does_not_ack(runner: R) -> None:
    """RC-ALO: replay returns events but does NOT acknowledge them."""
    _tmp, store, fake, _services = _mk_consumer_env()
    cid = "rc-alo"
    store.register_consumer(cid)
    evts = [await _publish(store, _StubBus(), "test.rc") for _ in range(3)]

    first = await fake.tools["consumer_event_pending_list"](consumer_id=cid)
    runner.assert_eq("RC-ALO-returned", first["returned"], 3)

    second = await fake.tools["consumer_event_pending_list"](consumer_id=cid)
    runner.assert_eq("RC-ALO-replay-again", second["returned"], 3)
    runner.assert_true("RC-ALO-at-least-once",
                       second["returned"] == first["returned"] == 3,
                       "replay must return the same events again (at-least-once)")
    ids_first = {e["id"] for e in first["events"]}
    ids_second = {e["id"] for e in second["events"]}
    runner.assert_eq("RC-ALO-same-events", ids_first, ids_second)


async def test_replay_does_not_advance_checkpoint(runner: R) -> None:
    """RC-NOCP: replay does NOT advance the consumer's checkpoint."""
    _tmp, store, fake, _services = _mk_consumer_env()
    cid = "rc-nocp"
    store.register_consumer(cid)
    for _ in range(3):
        await _publish(store, _StubBus(), "test.rc")

    cp_before = store.get_checkpoint(cid)
    await fake.tools["consumer_event_pending_list"](consumer_id=cid)
    cp_after = store.get_checkpoint(cid)
    runner.assert_eq("RC-NOCP-unchanged", cp_after, cp_before)


# ── Checkpoint monotonicity ───────────────────────────────────────────────────

async def test_checkpoint_monotonic(runner: R) -> None:
    """RC-MONO: ack advances the checkpoint monotonically."""
    _tmp, store, fake, _services = _mk_consumer_env()
    cid = "rc-mono"
    store.register_consumer(cid)
    evts = [await _publish(store, _StubBus(), "test.rc") for _ in range(3)]

    r1 = await fake.tools["consumer_event_acknowledge"](
        consumer_id=cid, event_id=evts[0]["id"])
    cp1 = r1["checkpoint"]
    r2 = await fake.tools["consumer_event_acknowledge"](
        consumer_id=cid, event_id=evts[1]["id"])
    cp2 = r2["checkpoint"]
    runner.assert_true("RC-MONO-advances", cp2 >= cp1, f"{cp2} < {cp1}")
    runner.assert_eq("RC-MONO-cp1", cp1, evts[0]["sequence"])
    runner.assert_eq("RC-MONO-cp2", cp2, evts[1]["sequence"])


# ── Multi-consumer independence ───────────────────────────────────────────────

async def test_multi_consumer_independence(runner: R) -> None:
    """RC-IND: consumers have independent checkpoints and ack state."""
    _tmp, store, fake, _services = _mk_consumer_env()
    cid_a = "rc-inda"
    cid_b = "rc-indb"
    store.register_consumer(cid_a)
    store.register_consumer(cid_b)
    evts = [await _publish(store, _StubBus(), "test.rc") for _ in range(3)]

    for e in evts:
        await fake.tools["consumer_event_acknowledge"](
            consumer_id=cid_a, event_id=e["id"])

    cp_a = store.get_checkpoint(cid_a)
    cp_b = store.get_checkpoint(cid_b)
    runner.assert_eq("RC-IND-a-advanced", cp_a, evts[-1]["sequence"])
    runner.assert_eq("RC-IND-b-untouched", cp_b, 0)

    pending_a = await fake.tools["consumer_event_pending_list"](consumer_id=cid_a)
    pending_b = await fake.tools["consumer_event_pending_list"](consumer_id=cid_b)
    runner.assert_eq("RC-IND-a-empty", pending_a["returned"], 0)
    runner.assert_eq("RC-IND-b-all", pending_b["returned"], 3)


# ── after_sequence=0 from-beginning ───────────────────────────────────────────

async def test_after_sequence_zero_from_beginning(runner: R) -> None:
    """RC-AFTER0: after_sequence=0 replays unacknowledged events from the beginning."""
    _tmp, store, fake, _services = _mk_consumer_env()
    cid = "rc-after0"
    store.register_consumer(cid)
    evts = [await _publish(store, _StubBus(), "test.rc") for _ in range(3)]

    # Ack the first event so the durable checkpoint advances past it.
    await fake.tools["consumer_event_acknowledge"](
        consumer_id=cid, event_id=evts[0]["id"])
    runner.assert_eq("RC-AFTER0-checkpoint",
                     store.get_checkpoint(cid), evts[0]["sequence"])

    # Default replay (from checkpoint) returns the remaining unacknowledged events.
    default = await fake.tools["consumer_event_pending_list"](consumer_id=cid)
    runner.assert_eq("RC-AFTER0-default-returned", default["returned"], 2)

    # after_sequence=0 is accepted and replays from the beginning of the
    # sequence space (checkpoint field = 0) — pending semantics preserved.
    from_zero = await fake.tools["consumer_event_pending_list"](
        consumer_id=cid, after_sequence=0)
    runner.assert_eq("RC-AFTER0-from-beginning", from_zero["returned"], 2)
    runner.assert_eq("RC-AFTER0-checkpoint-field", from_zero["checkpoint"], 0)
    ids = {e["id"] for e in from_zero["events"]}
    runner.assert_eq("RC-AFTER0-ids", ids, {e["id"] for e in evts[1:]})


# ── Pagination contract ───────────────────────────────────────────────────────

async def test_pagination_contract(runner: R) -> None:
    """RC-PAGE: returned/has_more/next_after_sequence pagination contract."""
    _tmp, store, fake, _services = _mk_consumer_env()
    cid = "rc-page"
    store.register_consumer(cid)
    evts = [await _publish(store, _StubBus(), "test.rc") for _ in range(5)]

    page1 = await fake.tools["consumer_event_pending_list"](
        consumer_id=cid, limit=2)
    runner.assert_eq("RC-PAGE-p1-returned", page1["returned"], 2)
    runner.assert_true("RC-PAGE-p1-has-more", page1["has_more"] is True)
    runner.assert_eq("RC-PAGE-p1-next", page1["next_after_sequence"],
                     evts[1]["sequence"])

    page2 = await fake.tools["consumer_event_pending_list"](
        consumer_id=cid, limit=2, after_sequence=page1["next_after_sequence"])
    runner.assert_eq("RC-PAGE-p2-returned", page2["returned"], 2)
    runner.assert_true("RC-PAGE-p2-has-more", page2["has_more"] is True)
    runner.assert_eq("RC-PAGE-p2-next", page2["next_after_sequence"],
                     evts[3]["sequence"])

    page3 = await fake.tools["consumer_event_pending_list"](
        consumer_id=cid, limit=2, after_sequence=page2["next_after_sequence"])
    runner.assert_eq("RC-PAGE-p3-returned", page3["returned"], 1)
    runner.assert_true("RC-PAGE-p3-has-more", page3["has_more"] is False)

    all_ids = [e["id"] for e in page1["events"] + page2["events"] + page3["events"]]
    runner.assert_eq("RC-PAGE-all-ids", all_ids, [e["id"] for e in evts])


# ── Canonical replay shape ────────────────────────────────────────────────────

async def test_pending_list_shape(runner: R) -> None:
    """RC-SHAPE: pending_list returns the canonical replay shape."""
    _tmp, store, fake, _services = _mk_consumer_env()
    cid = "rc-shape"
    store.register_consumer(cid)
    await _publish(store, _StubBus(), "test.rc")

    result = await fake.tools["consumer_event_pending_list"](consumer_id=cid)
    for key in ("consumer_id", "checkpoint", "returned", "has_more",
                "next_after_sequence", "events"):
        runner.assert_true(f"RC-SHAPE-{key}", key in result,
                           f"missing key {key}")
    runner.assert_eq("RC-SHAPE-consumer", result["consumer_id"], cid)
    runner.assert_eq("RC-SHAPE-checkpoint", result["checkpoint"], 0)
    runner.assert_eq("RC-SHAPE-returned", result["returned"], 1)
    runner.assert_true("RC-SHAPE-has-more", result["has_more"] is False)


# ── Replay input validation ───────────────────────────────────────────────────

async def test_replay_validation_errors(runner: R) -> None:
    """RC-VAL: replay input validation uses shared ValidationError."""
    _tmp, store, fake, _services = _mk_consumer_env()
    cid = "rc-val"
    store.register_consumer(cid)

    await _raises_async(runner, "RC-VAL-empty-consumer", ValidationError,
                        fake.tools["consumer_event_pending_list"],
                        consumer_id="  ")
    await _raises_async(runner, "RC-VAL-limit-zero", ValidationError,
                        fake.tools["consumer_event_pending_list"],
                        consumer_id=cid, limit=0)
    await _raises_async(runner, "RC-VAL-limit-negative", ValidationError,
                        fake.tools["consumer_event_pending_list"],
                        consumer_id=cid, limit=-5)
    await _raises_async(runner, "RC-VAL-after-negative", ValidationError,
                        fake.tools["consumer_event_pending_list"],
                        consumer_id=cid, after_sequence=-1)


# ── ACK error boundaries ──────────────────────────────────────────────────────

async def test_ack_error_boundaries(runner: R) -> None:
    """RC-ACK-ERR: ack failures use shared domain exceptions."""
    _tmp, store, fake, _services = _mk_consumer_env()
    cid = "rc-ackerr"
    store.register_consumer(cid)
    evt = await _publish(store, _StubBus(), "test.rc")

    # Unknown consumer
    await _raises_async(runner, "RC-ACK-ERR-consumer", ConsumerNotFoundError,
                        fake.tools["consumer_event_acknowledge"],
                        consumer_id="ghost", event_id=evt["id"])
    # Unknown event
    await _raises_async(runner, "RC-ACK-ERR-event", EventNotFoundError,
                        fake.tools["consumer_event_acknowledge"],
                        consumer_id=cid, event_id="nonexistent-event-id")
    # Irrelevant event (routed to a topic this consumer does not have)
    store.add_topic(cid, "alpha")
    other_evt = await _publish(store, _StubBus(), "test.rc",
                               routing={"topics": ["beta"]})
    await _raises_async(runner, "RC-ACK-ERR-irrelevant", EventNotRelevantError,
                        fake.tools["consumer_event_acknowledge"],
                        consumer_id=cid, event_id=other_evt["id"])


# ── Checkpoint get contract ───────────────────────────────────────────────────

async def test_checkpoint_get_contract(runner: R) -> None:
    """RC-CP-GET: checkpoint_get returns checkpoint + persisted updated_at."""
    _tmp, store, fake, _services = _mk_consumer_env()
    cid = "rc-cpget"
    store.register_consumer(cid)

    info = await fake.tools["consumer_checkpoint_get"](consumer_id=cid)
    runner.assert_eq("RC-CP-GET-checkpoint", info["checkpoint"], 0)
    runner.assert_true("RC-CP-GET-updated-at", bool(info.get("updated_at")),
                       "updated_at must be present and non-empty")

    await _raises_async(runner, "RC-CP-GET-consumer", ConsumerNotFoundError,
                        fake.tools["consumer_checkpoint_get"],
                        consumer_id="ghost")


# ── Market alert error boundaries ─────────────────────────────────────────────

async def test_market_alert_error_boundaries(runner: R) -> None:
    """RC-MA-ERR: market_alert failures use shared domain exceptions."""
    _tmp, _store, fake, _services = _mk_market_env()

    await _raises_async(runner, "RC-MA-ERR-operator", ValidationError,
                        fake.tools["market_alert_create"],
                        instrument_query="RELIANCE", operator="bogus",
                        threshold=100.0)
    await _raises_async(runner, "RC-MA-ERR-field", ValidationError,
                        fake.tools["market_alert_create"],
                        instrument_query="RELIANCE", operator="gt",
                        threshold=100.0, field="bogus")
    await _raises_async(runner, "RC-MA-ERR-threshold", ValidationError,
                        fake.tools["market_alert_create"],
                        instrument_query="RELIANCE", operator="gt",
                        threshold="high")
    await _raises_async(runner, "RC-MA-ERR-enable", AlertNotFoundError,
                        fake.tools["market_alert_enable"], alert_id=999999)
    await _raises_async(runner, "RC-MA-ERR-disable", AlertNotFoundError,
                        fake.tools["market_alert_disable"], alert_id=999999)
    await _raises_async(runner, "RC-MA-ERR-delete", AlertNotFoundError,
                        fake.tools["market_alert_delete"], alert_id=999999)


async def test_market_alert_no_instrument(runner: R) -> None:
    """RC-MA-NOINST: unresolvable instrument raises ValidationError."""
    _tmp, _store, fake, _services = _mk_market_env(results=[])
    await _raises_async(runner, "RC-MA-NOINST", ValidationError,
                        fake.tools["market_alert_create"],
                        instrument_query="NONEXISTENT", operator="gt",
                        threshold=100.0)


async def test_market_alert_services_unavailable(runner: R) -> None:
    """RC-MA-UNAVAIL: missing services raise StorageError, not error dicts."""
    tmp = tempfile.TemporaryDirectory()
    store = EventStore(os.path.join(tmp.name, "t.db"))
    fake = _FakeMCP()
    services = _Services(store)
    services.store = None  # simulate unavailable store
    from mcp_server.tools.market_alerts import register_market_alert_tools
    register_market_alert_tools(fake, services)

    await _raises_async(runner, "RC-MA-UNAVAIL-create", StorageError,
                        fake.tools["market_alert_create"],
                        instrument_query="RELIANCE", operator="gt",
                        threshold=100.0)
    await _raises_async(runner, "RC-MA-UNAVAIL-list", StorageError,
                        fake.tools["market_alert_list"])
    await _raises_async(runner, "RC-MA-UNAVAIL-enable", StorageError,
                        fake.tools["market_alert_enable"], alert_id=1)


# ── main ──────────────────────────────────────────────────────────────────────

async def main() -> bool:
    runner = R()

    await test_consumer_event_list_removed(runner)
    await test_replay_does_not_ack(runner)
    await test_replay_does_not_advance_checkpoint(runner)
    await test_checkpoint_monotonic(runner)
    await test_multi_consumer_independence(runner)
    await test_after_sequence_zero_from_beginning(runner)
    await test_pagination_contract(runner)
    await test_pending_list_shape(runner)
    await test_replay_validation_errors(runner)
    await test_ack_error_boundaries(runner)
    await test_checkpoint_get_contract(runner)
    await test_market_alert_error_boundaries(runner)
    await test_market_alert_no_instrument(runner)
    await test_market_alert_services_unavailable(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)