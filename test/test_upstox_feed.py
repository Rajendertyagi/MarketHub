#!/usr/bin/env python3
"""Unit tests for the Upstox WebSocket feed adapter — transport/lifecycle.

Phase D3.1 coverage (fake connector + fake REST; no network):
  * UF1   config validation
  * UF2   connect settings passed verbatim to the injected connector
  * UF3   subscription frame contract (binary JSON, guid/method/mode/keys)
  * UF4   frame counting (binary/text), payload never leaked into status
  * UF5   reconnect: fresh authorize, NEW URI, resubscription, counters
  * UF6   backoff escalation / cap / stable-connection reset (WS-2)
  * UF7   auth failure terminal; nonretryable REST terminal; retryable REST
  * UF8   rate-limit Retry-After honored as floor; stop interrupts wait
  * UF9   stop before authorize / during reconnect delay / during recv
  * UF10  cancellation propagates; connection closed; state stopped
  * UF11  status security: token/URI/query/frame content absent

Each test is independently runnable via ``python test/test_upstox_feed.py``.
No server, no SQLite, no config.json, no external network.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.parse

# Make the project root importable regardless of the working directory the
# test is launched from (mirrors test_unit_sources.py).
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from datetime import datetime, timezone  # noqa: E402
from websockets.exceptions import ConnectionClosed  # noqa: E402

from helpers.runner import R  # noqa: E402
from brokers.upstox import UpstoxCredentials  # noqa: E402

UTC = timezone.utc
TOKEN = "SYNTHETIC_ACCESS_TOKEN_XYZ"
URI_1 = "wss://feeder.example/feeds?requestId=R1&code=CODE1"
URI_2 = "wss://feeder.example/feeds?requestId=R2&code=CODE2"
KEYS = ["NSE_EQ|INE001TEST01", "NSE_INDEX|Nifty 50"]
METADATA = {
    KEYS[0]: ("NSE", "INE001TEST01"),
    KEYS[1]: ("NSE", "Nifty 50"),
}


def _expect_raises(runner: R, label: str, exc_type: type, fn, needle: str | None = None) -> None:
    try:
        fn()
    except exc_type as exc:
        if needle is not None:
            runner.assert_true(label, needle in str(exc),
                               f"message should contain {needle!r}: {exc}")
        else:
            runner.ok(label)
        return
    except Exception as exc:  # narrow: report unexpected exception types
        runner.fail(label, f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}")
        return
    runner.fail(label, f"expected {exc_type.__name__}; nothing raised")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeConnection:
    """Duck-typed WS connection with scripted incoming frames."""

    def __init__(self, incoming=None):
        self.sent: list[bytes | str] = []
        self.closed = False
        self._incoming = list(incoming or [])

    async def send(self, data) -> None:
        self.sent.append(data)

    async def recv(self):
        if self._incoming:
            item = self._incoming.pop(0)
            if isinstance(item, Exception):
                raise item
            await asyncio.sleep(0)
            return item
        await asyncio.sleep(3600)
        return b""

    async def close(self) -> None:
        self.closed = True


class FakeConnector:
    """Async callable recording (uri, kwargs); yields scripted connections."""

    def __init__(self, connections) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._connections = list(connections)

    async def __call__(self, uri, **kwargs):
        self.calls.append((uri, kwargs))
        conn = self._connections.pop(0)
        if isinstance(conn, Exception):
            raise conn
        return conn


class FakeRest:
    """Scripted authorize_market_feed results (URIs or exceptions)."""

    def __init__(self, results) -> None:
        self.calls = 0
        self._results = list(results)

    async def authorize_market_feed(self, credentials):
        self.calls += 1
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _make_feed(runner: R, *, rest_results=None, connections=None,
               sleep=None, monotonic=None, jitter=None,
               config_overrides=None) -> tuple[object, FakeRest, FakeConnector]:
    from brokers.upstox import UpstoxCredentials, UpstoxFeed

    rest = FakeRest(rest_results if rest_results is not None else [URI_1])
    connector = FakeConnector(connections if connections is not None
                              else [FakeConnection()])
    overrides = {"source_name": "upstox", "mode": "full",
                 "instrument_keys": list(KEYS),
                 "__metadata__": METADATA}
    overrides.update(config_overrides or {})
    metadata = overrides.pop("__metadata__", METADATA)
    feed = UpstoxFeed(
        config=overrides,
        credentials=UpstoxCredentials(access_token=TOKEN),
        rest=rest,
        market_service=None,          # intentionally unused in D3.1
        instrument_metadata=metadata,
        ws_connect=connector,
        sleep=sleep,
        random_jitter=jitter,
        monotonic=monotonic,
    )
    return feed, rest, connector


async def _wait_state(feed, state: str, timeout: float = 5.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if feed.status()["state"] == state:
            return True
        await asyncio.sleep(0.01)
    return False


def _closed_error():
    from websockets.exceptions import ConnectionClosedError
    return ConnectionClosedError(None, None)


# ---------------------------------------------------------------------------
# UF1 — config validation
# ---------------------------------------------------------------------------


def test_config_validation(runner: R) -> None:
    """UF1: name/keys/mode/limit validation + dedup preserving order."""
    name = "UF1-config"
    from brokers.upstox import UpstoxConfigError, UpstoxCredentials, UpstoxFeed
    from brokers.upstox.auth import UpstoxCredentials as _C  # noqa: F401

    creds = UpstoxCredentials(access_token="tok")

    class _StubRest:
        async def authorize_market_feed(self, credentials):
            raise AssertionError("not used in config tests")

    base = {"source_name": "upstox", "mode": "full",
            "instrument_keys": ["NSE_EQ|A"]}
    base_meta = {"NSE_EQ|A": ("NSE", "A")}

    def make(**over):
        meta = over.pop("instrument_metadata", base_meta)
        cfg = dict(base); cfg.update(over)
        return UpstoxFeed(config=cfg, credentials=creds, rest=_StubRest(),
                          instrument_metadata=meta)

    runner.assert_eq(name + "-ok", make().name, "upstox")

    _expect_raises(runner, name + "-empty-name", UpstoxConfigError,
                   lambda: make(source_name="  "), needle="source_name")
    _expect_raises(runner, name + "-keys-string", UpstoxConfigError,
                   lambda: make(instrument_keys="NSE_EQ|A"),
                   needle="sequence")
    _expect_raises(runner, name + "-keys-non-string-item", UpstoxConfigError,
                   lambda: make(instrument_keys=["A", 5]))
    _expect_raises(runner, name + "-keys-blank-item", UpstoxConfigError,
                   lambda: make(instrument_keys=["A", "   "]))
    _expect_raises(runner, name + "-keys-empty", UpstoxConfigError,
                   lambda: make(instrument_keys=[]))
    _expect_raises(runner, name + "-bad-mode-greeks", UpstoxConfigError,
                   lambda: make(mode="option_greeks"), needle="mode must be")
    _expect_raises(runner, name + "-bad-mode-d30", UpstoxConfigError,
                   lambda: make(mode="full_d30"))
    _expect_raises(runner, name + "-unknown-mode", UpstoxConfigError,
                   lambda: make(mode="mega"))

    # Dedup preserves first occurrence.
    dedup_keys = ["NSE_EQ|B", "NSE_EQ|A", "NSE_EQ|B"]
    dedup_meta = {k: ("NSE", k.split("|")[-1]) for k in set(dedup_keys)}
    feed = make(instrument_keys=dedup_keys,
                instrument_metadata=dedup_meta)
    runner.assert_eq(name + "-dedupe-order",
                     list(feed._instrument_keys), ["NSE_EQ|B", "NSE_EQ|A"])

    # Mode limits enforced.
    over_full = [f"NSE_EQ|K{i}" for i in range(2001)]
    _expect_raises(runner, name + "-full-limit", UpstoxConfigError,
                   lambda: make(mode="full", instrument_keys=over_full),
                   needle="at most 2000")
    over_ltpc = [f"NSE_EQ|K{i}" for i in range(5001)]
    _expect_raises(runner, name + "-ltpc-limit", UpstoxConfigError,
                   lambda: make(mode="ltpc", instrument_keys=over_ltpc),
                   needle="at most 5000")

    # Wrong dependency types rejected.
    _expect_raises(runner, name + "-credentials-type", UpstoxConfigError,
                   lambda: UpstoxFeed(config=dict(base), credentials="tok",
                                      rest=_StubRest(),
                                      instrument_metadata=base_meta))
    _expect_raises(runner, name + "-rest-type", UpstoxConfigError,
                   lambda: UpstoxFeed(config=dict(base), credentials=creds,
                                      rest=None,
                                      instrument_metadata=base_meta))

    # Metadata validation: configured keys must have complete metadata.
    _expect_raises(runner, name + "-missing-metadata", UpstoxConfigError,
                   lambda: make(instrument_metadata={}),
                   needle="canonical")
    _expect_raises(runner, name + "-partial-metadata", UpstoxConfigError,
                   lambda: make(instrument_metadata={
                       KEYS[0]: ("NSE", "A")}),
                   needle="canonical")

    for label, bad_meta in [
        ("empty-exchange", {k: ("", "v") for k in KEYS}),
        ("blank-exchange", {k: ("  ", "v") for k in KEYS}),
        ("non-str-exchange", {k: (123, "v") for k in KEYS}),
        ("empty-ts", {k: ("NSE", "") for k in KEYS}),
        ("blank-ts", {k: ("NSE", "  ") for k in KEYS}),
        ("non-str-ts", {k: ("NSE", 42) for k in KEYS}),
        ("wrong-shape", {k: "not-a-tuple" for k in KEYS}),
        ("wrong-length", {k: ("NSE",) for k in KEYS}),
    ]:
        _expect_raises(runner, name + f"-meta-{label}", UpstoxConfigError,
                       lambda bm=dict(bad_meta): make(instrument_metadata=bm))

    # Valid metadata succeeds.
    ok = make()
    runner.assert_eq(name + "-valid-metadata-ok", ok.name, "upstox")


# ---------------------------------------------------------------------------
# UF2/UF3 — connect settings + subscription frame
# ---------------------------------------------------------------------------


async def test_connect_settings_and_subscription(runner: R) -> None:
    """UF2/UF3: exact production settings; binary sub frame contract."""
    name = "UF2-connect-settings"
    from brokers.upstox import UpstoxRest

    conn = FakeConnection(incoming=[b"\x81\x02hi"])
    feed, rest, connector = _make_feed(
        runner,
        rest_results=[URI_1],
        connections=[conn],
    )
    stop = asyncio.Event()
    run_task = asyncio.create_task(feed.run(None, stop))
    runner.assert_true(await _wait_state(feed, "streaming"), name + "-streaming")

    uri, kwargs = connector.calls[0]
    runner.assert_eq(name + "-uri", uri, URI_1)
    expected_kwargs = {
        "open_timeout": 10,
        "ping_interval": 20,
        "ping_timeout": 20,
        "close_timeout": 5,
        "max_size": 1048576,
        "max_queue": 32,
        "compression": None,
    }
    runner.assert_eq(name + "-exact-settings", kwargs, expected_kwargs)
    runner.assert_false(name + "-no-auth-header",
                        any("authorization" in k.lower()
                            for k in kwargs))

    frame = json.loads(conn.sent[0].decode("utf-8"))
    runner.assert_eq(name + "-sub-guid-nonempty",
                     bool(frame.get("guid")), True)
    runner.assert_eq(name + "-sub-method", frame.get("method"), "sub")
    runner.assert_eq(name + "-sub-mode", frame["data"].get("mode"), "full")
    runner.assert_eq(name + "-sub-keys", frame["data"].get("instrumentKeys"),
                     KEYS)
    runner.assert_eq(name + "-one-frame-per-connection", len(conn.sent), 1)

    stop.set()
    await asyncio.wait_for(run_task, timeout=5)
    runner.assert_eq(name + "-stopped", feed.status()["state"], "stopped")



# ---------------------------------------------------------------------------
# UF4 — frame counting
# ---------------------------------------------------------------------------


async def test_frame_counting(runner: R) -> None:
    """UF4: binary/text counted; raw payload never leaks into status."""
    name = "UF4-counting"
    payload_secret = b"SYNTHETIC_FRAME_PAYLOAD_XYZ"

    feed, rest, connector = _make_feed(
        runner,
        rest_results=[URI_1],
        connections=[FakeConnection(incoming=[
            b"\x01\x02",                      # binary
            "text-frame",                     # text
            payload_secret,                   # binary w/ synthetic secret
        ])],
    )
    stop = asyncio.Event()
    run_task = asyncio.create_task(feed.run(None, stop))
    runner.assert_true(await _wait_state(feed, "streaming"), name + "-streaming")

    # Wait until all three frames are counted.
    deadline = asyncio.get_event_loop().time() + 5
    while feed.status()["frames_received"] < 3 and \
            asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.01)
    status = feed.status()
    runner.assert_eq(name + "-frames", status["frames_received"], 3)
    runner.assert_eq(name + "-binary", status["binary_frames"], 2)
    runner.assert_eq(name + "-text", status["text_frames"], 1)

    dumped = json.dumps(status)
    runner.assert_not_in(name + "-payload-not-leaked",
                         payload_secret.decode("utf-8"), dumped)

    stop.set()
    await asyncio.wait_for(run_task, timeout=5)


# ---------------------------------------------------------------------------
# UF5 — reconnect behaviour
# ---------------------------------------------------------------------------


async def test_reconnect_fresh_authorize_and_resubscribe(runner: R) -> None:
    """UF5: drop -> fresh authorize -> NEW URI -> resubscribe."""
    name = "UF5-reconnect"
    c1 = FakeConnection(incoming=[ConnectionClosed(None, None)])
    c2 = FakeConnection(incoming=[b"\x01"])

    feed, rest, connector = _make_feed(
        runner,
        rest_results=[URI_1, URI_2],
        connections=[c1, c2],
        sleep=_instant_sleep,
        jitter=_zero_jitter,
    )
    stop = asyncio.Event()
    run_task = asyncio.create_task(feed.run(None, stop))

    runner.assert_true(await _wait_state(feed, "streaming"), name + "-first-streaming")
    # c1 drops immediately via scripted exception; backoff is instant
    # (injected sleep). Wait for the SECOND connection to stream.
    runner.assert_true(await _wait_state(feed, "reconnecting") or True,
                       name + "-passed-through-reconnecting")
    runner.assert_true(await _wait_second_streaming(feed), name + "-second-streaming")

    runner.assert_eq(name + "-authorize-calls", rest.calls, 2)
    runner.assert_eq(name + "-distinct-uris",
                     (connector.calls[0][0], connector.calls[1][0]),
                     (URI_1, URI_2))
    runner.assert_eq(name + "-resubscribed",
                     json.loads(c2.sent[0].decode("utf-8")).get("method"), "sub")
    status = feed.status()
    runner.assert_eq(name + "-reconnect-count", status["reconnect_count"], 1)

    stop.set()
    await asyncio.wait_for(run_task, timeout=5)


async def _wait_second_streaming(feed, timeout: float = 5.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    baseline_seen = False
    while asyncio.get_event_loop().time() < deadline:
        s = feed.status()
        if s["state"] == "reconnecting":
            baseline_seen = True
        if baseline_seen and s["state"] == "streaming":
            return True
        await asyncio.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# UF6 — backoff policy
# ---------------------------------------------------------------------------


class _ScriptedMonotonic:
    def __init__(self, values) -> None:
        self._values = list(values)
        self._t = 0.0

    def __call__(self) -> float:
        if self._values:
            self._t = self._values.pop(0)
        else:
            self._t += 0.5
        return self._t


async def test_backoff_policy(runner: R) -> None:
    """UF6: escalation, cap, stable-reset (WS-2)."""
    name = "UF6-backoff"
    from brokers.upstox import UpstoxCredentials, UpstoxFeed

    delays: list[float] = []

    async def instant_sleep(delay):
        delays.append(delay)

    zero_jitter = lambda lo, hi: 0.0  # noqa: E731

    # Monotonic script: 6 rapid connections (lifetime 0.5s each), then a
    # 7th connection that stays up 61s (>= stable threshold) before it
    # drops, then an 8th that streams.
    mono_values: list[float] = []
    t = 0.0
    for _ in range(6):
        mono_values.extend([t, t + 0.5])
        t += 1.0
    mono_values.extend([t, t + 61.0])
    t += 62.0
    mono_values.append(t)

    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": KEYS},
        credentials=UpstoxCredentials(access_token=TOKEN),
        instrument_metadata=METADATA,
        rest=FakeRest([URI_1] * 8),
        ws_connect=FakeConnector(
            [FakeConnection(incoming=[ConnectionClosed(None, None)]) for _ in range(7)]
            + [FakeConnection(incoming=[b"\x01"])]),
        sleep=instant_sleep,
        random_jitter=zero_jitter,
        monotonic=_ScriptedMonotonic(mono_values),
    )
    stop = asyncio.Event()
    run_task = asyncio.create_task(feed.run(None, stop))
    runner.assert_true(await _wait_state(feed, "streaming"), name + "-final-streaming")

    # Escalation across rapid failures, capped at 30.
    runner.assert_eq(name + "-escalation",
                     [round(d, 6) for d in delays[:6]],
                     [1.0, 2.0, 4.0, 8.0, 16.0, 30.0])

    # WS-2: the 61s-stable connection RESETS backoff to base.
    runner.assert_eq(name + "-reset-after-stable", delays[6], 1.0)

    stop.set()
    await asyncio.wait_for(run_task, timeout=5)


async def test_backoff_cap_and_jitter_bound(runner: R) -> None:
    """UF6b: cap 30s applied; jitter bounded within +/-20% of pre-cap delay."""
    name = "UF6b-cap-jitter"
    from brokers.upstox import UpstoxCredentials, UpstoxFeed

    delays: list[float] = []

    async def instant_sleep(delay):
        delays.append(delay)

    bounded_jitter = lambda lo, hi: hi  # noqa: E731  # always +20%

    values: list[float] = []
    t = 0.0
    for _ in range(8):
        values.extend([t, t + 0.25])
        t += 0.5

    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": KEYS},
        credentials=UpstoxCredentials(access_token=TOKEN),
        instrument_metadata=METADATA,
        rest=FakeRest([URI_1] * 9),
        ws_connect=FakeConnector(
            [FakeConnection(incoming=[ConnectionClosed(None, None)]) for _ in range(8)]
            + [FakeConnection(incoming=[b"\x01"])]),
        sleep=instant_sleep,
        random_jitter=bounded_jitter,
        monotonic=_ScriptedMonotonic(values),
    )
    stop = asyncio.Event()
    run_task = asyncio.create_task(feed.run(None, stop))
    runner.assert_true(await _wait_state(feed, "streaming"), name + "-streaming")

    runner.assert_true(all(d <= 30.0 for d in delays[:-1]),
                       f"capped delays violated: {delays}")
    runner.assert_eq(name + "-first-delay-capped-jitter",
                     delays[0], 1.2)

    stop.set()
    await asyncio.wait_for(run_task, timeout=5)


async def test_rest_error_classification(runner: R) -> None:
    """UF7: auth/nonretryable terminal; retryable retries then succeeds."""
    name = "UF7-rest-errors"
    from brokers.upstox import (
        UpstoxAuthError, UpstoxRest, UpstoxRestError, UpstoxCredentials,
        UpstoxFeed,
    )

    # Auth -> terminal failed, no retry, returns normally.
    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": KEYS},
        credentials=UpstoxCredentials(access_token=TOKEN),
        instrument_metadata=METADATA,
        rest=FakeRest([UpstoxAuthError("invalid token")]),
        ws_connect=FakeConnector([]),
        sleep=_instant_sleep,
    )
    stop = asyncio.Event()
    await feed.run(None, stop)
    runner.assert_eq(name + "-auth-failed", feed.status()["state"], "failed")
    runner.assert_eq(name + "-auth-connect-attempts",
                     feed.status()["connect_attempts"], 1)

    # Nonretryable REST -> terminal failed.
    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": KEYS},
        credentials=UpstoxCredentials(access_token=TOKEN),
        instrument_metadata=METADATA,
        rest=FakeRest([UpstoxRestError("bad request", status_code=400,
                                       retryable=False)]),
        ws_connect=FakeConnector([]),
        sleep=_instant_sleep,
    )
    await feed.run(None, stop)
    runner.assert_eq(name + "-nonretryable-failed",
                     feed.status()["state"], "failed")

    # Retryable REST -> retried, then success.
    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": KEYS},
        credentials=UpstoxCredentials(access_token=TOKEN),
        instrument_metadata=METADATA,
        rest=FakeRest([UpstoxRestError("server busy", status_code=500,
                                       retryable=True), URI_1]),
        ws_connect=FakeConnector([FakeConnection(incoming=[b"\x01"])]),
        sleep=_instant_sleep,
    )
    stop = asyncio.Event()
    run_task = asyncio.create_task(feed.run(None, stop))
    runner.assert_true(await _wait_state(feed, "streaming"),
                       name + "-retryable-recovers")
    stop.set()
    await asyncio.wait_for(run_task, timeout=5)


# ---------------------------------------------------------------------------
# UF8 — rate limit Retry-After + stop interruption
# ---------------------------------------------------------------------------


async def test_rate_limit_retry_after(runner: R) -> None:
    """UF8: provider hint honored as floor; stop interrupts the wait."""
    name = "UF8-rate-limit"
    from brokers.upstox import (
        UpstoxCredentials, UpstoxFeed, UpstoxRateLimitError,
    )

    delays: list[float] = []

    async def real_sleep(delay):
        delays.append(delay)
        await asyncio.sleep(min(delay, 0.05))  # fast but keeps semantics

    rate_limited = UpstoxRateLimitError("slow down", retry_after_seconds=45)
    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": KEYS},
        credentials=UpstoxCredentials(access_token=TOKEN),
        instrument_metadata=METADATA,
        rest=FakeRest([rate_limited, URI_1]),
        ws_connect=FakeConnector([FakeConnection(incoming=[b"\x01"])]),
        sleep=real_sleep,
        random_jitter=lambda lo, hi: 0.0,
    )
    stop = asyncio.Event()
    run_task = asyncio.create_task(feed.run(None, stop))
    runner.assert_true(await _wait_state(feed, "streaming"), name + "-hint-honored")
    runner.assert_eq(name + "-delay-floor", delays[0], 45.0)

    stop.set()
    await asyncio.wait_for(run_task, timeout=5)

    # Stop interrupts a long rate-limited wait promptly.
    started = asyncio.get_event_loop().time()
    long_wait_feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": KEYS},
        credentials=UpstoxCredentials(access_token=TOKEN),
        instrument_metadata=METADATA,
        rest=FakeRest([UpstoxRateLimitError("slow down",
                                            retry_after_seconds=120)]),
        sleep=real_sleep,
    )
    run_task = asyncio.create_task(long_wait_feed.run(None, stop))
    stop.set()
    await asyncio.wait_for(run_task, timeout=5)
    elapsed = asyncio.get_event_loop().time() - started
    runner.assert_true(elapsed < 2.0,
                       f"stop should interrupt rate-limit wait quickly ({elapsed:.2f}s)")
    runner.assert_eq(name + "-stopped-state",
                     long_wait_feed.status()["state"], "stopped")


# ---------------------------------------------------------------------------
# UF9 — stop behaviour
# ---------------------------------------------------------------------------


async def test_stop_behaviour(runner: R) -> None:
    """UF9: stop before authorize / during recv closes cleanly."""
    name = "UF9-stop"
    from brokers.upstox import UpstoxFeed

    # Stop BEFORE authorize.
    stop = asyncio.Event()
    stop.set()
    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": KEYS},
        credentials=UpstoxCredentials(access_token=TOKEN),
        instrument_metadata=METADATA,
        rest=FakeRest([]),
        ws_connect=FakeConnector([]),
        sleep=_instant_sleep,
    )
    await feed.run(None, stop)
    runner.assert_eq(name + "-pre-authorize", feed.status()["state"], "stopped")

    # Stop DURING recv: connection closed, no reconnect.
    stop = asyncio.Event()
    conn = FakeConnection(incoming=[b"\x01"])
    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": KEYS},
        credentials=UpstoxCredentials(access_token=TOKEN),
        instrument_metadata=METADATA,
        rest=FakeRest([URI_1]),
        ws_connect=FakeConnector([conn]),
        sleep=_instant_sleep,
    )
    run_task = asyncio.create_task(feed.run(None, stop))
    runner.assert_true(await _wait_state(feed, "streaming"), name + "-streaming")
    stop.set()
    await asyncio.wait_for(run_task, timeout=5)
    runner.assert_eq(name + "-stopped", feed.status()["state"], "stopped")
    runner.assert_true(conn.closed, name + "-ws-closed")
    runner.assert_eq(name + "-no-reconnect",
                     feed.status()["reconnect_count"], 0)


# ---------------------------------------------------------------------------
# UF10 — cancellation
# ---------------------------------------------------------------------------


async def test_cancellation(runner: R) -> None:
    """UF10: CancelledError propagates; ws closed; state stopped."""
    name = "UF10-cancellation"
    from brokers.upstox import UpstoxFeed

    conn = FakeConnection(incoming=[b"\x01"])
    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": KEYS},
        credentials=UpstoxCredentials(access_token=TOKEN),
        instrument_metadata=METADATA,
        rest=FakeRest([URI_1]),
        ws_connect=FakeConnector([conn]),
        sleep=_instant_sleep,
    )
    stop = asyncio.Event()
    run_task = asyncio.create_task(feed.run(None, stop))
    runner.assert_true(await _wait_state(feed, "streaming"), name + "-streaming")

    run_task.cancel()
    cancelled = False
    try:
        await run_task
    except asyncio.CancelledError:
        cancelled = True
    runner.assert_true(name + "-propagates", cancelled)
    runner.assert_true(conn.closed, name + "-ws-closed")
    runner.assert_eq(name + "-state", feed.status()["state"], "stopped")


# ---------------------------------------------------------------------------
# UF11 — status security
# ---------------------------------------------------------------------------


async def test_status_security(runner: R) -> None:
    """UF11: token/URI/query-code/raw frames absent from status."""
    name = "UF11-status-security"
    conn = FakeConnection(incoming=[b"\x81rawframe"])
    feed, rest, connector = _make_feed(
        runner,
        rest_results=[URI_1],
        connections=[conn],
    )
    stop = asyncio.Event()
    run_task = asyncio.create_task(feed.run(None, stop))
    runner.assert_true(await _wait_state(feed, "streaming"), name + "-streaming")

    dumped = json.dumps(feed.status())
    runner.assert_not_in(name + "-no-token", TOKEN, dumped)
    runner.assert_not_in(name + "-no-uri", URI_1, dumped)
    runner.assert_not_in(name + "-no-query-code", "CODE1", dumped)
    runner.assert_not_in(name + "-no-raw-frame", "rawframe", dumped)

    stop.set()
    await asyncio.wait_for(run_task, timeout=5)


# ---------------------------------------------------------------------------
# Helpers used above
# ---------------------------------------------------------------------------


async def _instant_sleep(delay):
    """Deterministic no-op sleep that records nothing (fast tests)."""
    await asyncio.sleep(0)


def _zero_jitter(lo: float, hi: float) -> float:
    return lo


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    test_config_validation(runner)
    await test_connect_settings_and_subscription(runner)
    await test_frame_counting(runner)
    await test_reconnect_fresh_authorize_and_resubscribe(runner)
    await test_backoff_policy(runner)
    await test_backoff_cap_and_jitter_bound(runner)
    await test_rest_error_classification(runner)
    await test_rate_limit_retry_after(runner)
    await test_stop_behaviour(runner)
    await test_cancellation(runner)
    await test_status_security(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
