#!/usr/bin/env python3
"""Fyers live feed lifecycle tests (FF1-FF11) — HSM binary protocol.

  * FF1   registry contains fyers_feed factory
  * FF2   factory requires access_token_getter (honest config error)
  * FF3   connect + binary auth + mode + subscribe frames on start
  * FF4   binary snapshot tick -> canonical QuotePatch in MarketService
  * FF5   malformed frame isolated (loop continues, counter increments)
  * FF6   add/remove while streaming sends binary sub/unsub frames
  * FF7   stop event ends session cleanly (state stopped)
  * FF8   transport failure triggers reconnect state, not terminal
  * FF9   status exposes safe counters only
  * FF10  JWT hsm_key extraction
  * FF11  terminal sentinel exits cleanly (no isinstance crash)

NO LIVE BROKER. Stub websocket speaking the HSM binary wire format.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R

_TOPIC = "sf|nse_cm|1234"


def _jwt_with_hsm_key(hsm_key: str = "HSMSYNTH") -> str:
    """Synthetic access-token JWT carrying an hsm_key claim."""
    def b64(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{b64({'alg': 'none'})}.{b64({'hsm_key': hsm_key})}."


def _auth_ok_frame() -> bytes:
    """HSM auth response with 'K' (ok) at the SDK-parsed offset."""
    buf = bytearray()
    buf += struct.pack("!H", 15)          # length
    buf.append(1)                          # resp_type: auth
    buf.append(0)                          # skipped byte
    buf.append(0)                          # skipped byte
    buf += struct.pack("!H", 1)            # field length
    buf += b"K"                            # ok
    buf += b"\x00\x00\x00"                 # filler
    buf += struct.pack(">I", 5)            # ack_count
    return bytes(buf)


def _snapshot_frame(topic_id: int, name: str, fields: dict[int, int],
                    multiplier: int = 1, precision: int = 1,
                    max_field: int = 20) -> bytes:
    """Binary datafeed snapshot (resp_type 6, record type 83).

    Values are POSITIONAL per the official field order; absent fields use
    the -2147483648 sentinel exactly like the wire format.
    """
    body = bytearray()
    body += struct.pack("!H", 0)           # placeholder length
    body.append(6)                         # resp_type: datafeed
    body += struct.pack(">I", 1)           # message number
    body += struct.pack("!H", 1)           # scrip count
    body.append(83)                        # record type: snapshot
    body += struct.pack("H", topic_id)
    body.append(len(name))
    body += name.encode()
    count = max(fields) + 1 if fields else 0
    body.append(count)
    for idx in range(count):
        body += struct.pack(">i", fields.get(idx, -2147483648))
    # The official SDK skips TWO bytes between the value array and the
    # multiplier (fyers_apiv3/FyersWebsocket/data_ws.py); the wire layout
    # is part of the protocol, so the fixture reproduces it.
    body += b"\x00\x00"
    body += struct.pack(">H", multiplier)
    body.append(precision)
    for s in ("NSE", "1234", "SBIN"):
        body.append(len(s))
        body += s.encode()
    frame = bytearray(body)
    frame[0:2] = struct.pack("!H", len(frame) - 2)
    return bytes(frame)


def _delta_frame(topic_id: int, fields: dict[int, int]) -> bytes:
    """Binary datafeed delta (resp_type 6, record type 85), positional."""
    body = bytearray()
    body += struct.pack("!H", 0)
    body.append(6)
    body += struct.pack(">I", 2)
    body += struct.pack("!H", 1)
    body.append(85)
    body += struct.pack("H", topic_id)
    count = max(fields) + 1 if fields else 0
    body.append(count)
    for idx in range(count):
        body += struct.pack(">i", fields.get(idx, -2147483648))
    frame = bytearray(body)
    frame[0:2] = struct.pack("!H", len(frame) - 2)
    return bytes(frame)


class _StubWS:
    """Stub socket that answers the binary handshake automatically."""

    def __init__(self):
        self.sent: list[bytes] = []
        self.incoming: list[bytes] = []
        self.closed = False

    async def send(self, data):
        self.sent.append(bytes(data))
        # Auto-respond to the auth frame so the handshake completes.
        if len(data) >= 3 and data[2] == 1:
            self.incoming.insert(0, _auth_ok_frame())

    async def recv(self):
        if self.incoming:
            return self.incoming.pop(0)
        await asyncio.sleep(3600)
        return ""

    async def close(self):
        self.closed = True


def _mk_feed(market_service=None, instrument_keys=None,
             wire_resolver=None):
    from brokers.fyers.feed import FyersFeed

    ws = _StubWS()
    keys = instrument_keys or ["NSE:SBIN-EQ"]

    async def ws_connect(token):
        assert token.startswith("eyJ")      # synthetic JWT
        return ws

    cfg = {"source_name": "fyers",
           "instrument_keys": keys,
           "app_id": "APP-1",
           "access_token_getter": lambda: _jwt_with_hsm_key("HSMSYNTH"),
           "ws_connect": ws_connect,
           "wire_symbol_resolver": wire_resolver,
           "utc_now_iso": lambda: "2026-08-24T10:00:00+00:00"}
    feed = FyersFeed(config=cfg, auth=object(),
                     market_service=market_service)
    # Pre-seed the HSM symbol map so tests never hit the REST converter.
    feed._hsm_symbols = {k: _TOPIC for k in keys}
    feed._hsm_by_topic = {_TOPIC: keys[0]}
    return feed, ws


async def _run_briefly(feed, seconds=0.4):
    stop = asyncio.Event()
    task = asyncio.create_task(feed.run(None, stop))
    await asyncio.sleep(seconds)
    stop.set()
    try:
        await asyncio.wait_for(task, timeout=3)
    except asyncio.TimeoutError:
        task.cancel()


# -- tests ---------------------------------------------------------------------


async def test_ff1_registry(runner: R) -> None:
    from sources.registry import SOURCE_TYPES
    runner.assert_in("FF1-fyers-in-registry", "fyers_feed", SOURCE_TYPES)


async def test_ff2_factory_requires_getter(runner: R) -> None:
    from sources.registry import SOURCE_TYPES
    try:
        SOURCE_TYPES["fyers_feed"]({"instrument_keys": ["X"]})
        ok = False
    except ValueError:
        ok = True
    runner.assert_true("FF2-honest-config-error", ok)


async def test_ff3_to_ff5_lifecycle(runner: R) -> None:
    from market.service import MarketService

    svc = MarketService()
    # Binary snapshot: ltp(idx0)=8105 @scale10 -> 810.5; vol(idx1)=1000;
    # prev_close(idx20)=8000 -> 800.0. Then a malformed short frame.
    snap = _snapshot_frame(101, _TOPIC, {0: 8105, 1: 1000, 20: 8000})
    bad = b"\x00\x02\x06\x00\x01"       # truncated datafeed frame
    delta = _delta_frame(101, {0: 8200})
    feed, ws = _mk_feed(market_service=svc)
    ws.incoming = [snap, bad, delta]

    await _run_briefly(feed, seconds=0.6)

    # FF3: auth + mode + subscribe frames sent (in order).
    runner.assert_eq("FF3-auth-frame", ws.sent[0][2], 1)
    runner.assert_eq("FF3-mode-frame", ws.sent[1][2], 12)
    runner.assert_eq("FF3-sub-frame", ws.sent[2][2], 4)
    runner.assert_in("FF3-sub-hsm-symbol", _TOPIC.encode(), ws.sent[2])

    # FF4: canonical quote reached MarketService — snapshot 810.5 then
    # binary delta updated it to 820.0 (both paths proven).
    q = await svc.get_quote("NSE", "NSE:SBIN-EQ")
    runner.assert_true("FF4-quote-applied", q is not None)
    if q:
        runner.assert_eq("FF4-ltp-scaled", float(q.ltp), 820.0)

    # FF5: malformed frame did not kill the loop; delta still applied.
    st = feed.status()
    runner.assert_eq("FF5-malformed-count", st["malformed_frames"], 1)
    runner.assert_ge("FF5-frames-received", st["frames_received"], 3)


async def test_ff6_delta_frames(runner: R) -> None:
    from brokers.fyers.hsm_symbols import Resolution

    def _stub_resolver(symbols, token):
        """Stub wire resolver: unit tests never touch the network."""
        res = Resolution()
        for sym in symbols:
            res.resolved[sym] = f"sf|nse_cm|{abs(hash(sym)) % 10000:04d}"
        return res

    feed, ws = _mk_feed(wire_resolver=_stub_resolver)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.3)
        added = await feed.add_instruments(["NSE:TCS-EQ"])
        removed = await feed.remove_instruments(["NSE:TCS-EQ"])
        stop.set()
        await asyncio.gather(task, return_exceptions=True)
        return added, removed

    added, removed = await run()
    runner.assert_eq("FF6-add-ok", added, 1)
    runner.assert_eq("FF6-remove-ok", removed, 1)
    subs = [f for f in ws.sent if len(f) >= 3 and f[2] == 4]
    unsubs = [f for f in ws.sent if len(f) >= 3 and f[2] == 5]
    runner.assert_ge("FF6-binary-sub-sent", len(subs), 1)
    runner.assert_ge("FF6-binary-unsub-sent", len(unsubs), 1)


async def test_ff7_clean_stop(runner: R) -> None:
    feed, ws = _mk_feed()

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.3)
        stop.set()
        await asyncio.wait_for(task, timeout=3)

    await run()
    runner.assert_eq("FF7-stopped-state", feed.status()["state"], "stopped")
    runner.assert_true("FF7-ws-closed", ws.closed)


async def test_ff8_transport_failure_reconnects(runner: R) -> None:
    """Transport error -> reconnecting/backoff, never terminal failed."""
    from brokers.fyers.feed import FyersFeed

    class _BrokenWS:
        async def send(self, data):
            raise ConnectionError("boom")

        async def recv(self):
            raise ConnectionError("boom")

        async def close(self):
            pass

    cfg = {"source_name": "fyers", "instrument_keys": ["NSE:X"],
           "app_id": "A", "access_token_getter": lambda: "T",
           "ws_connect": lambda token: asyncio.sleep(0, result=_BrokenWS()),
           "utc_now_iso": lambda: ""}
    feed = FyersFeed(config=cfg, auth=object(), market_service=None)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.3)
        stop.set()
        await asyncio.gather(task, return_exceptions=True)

    await run()
    st = feed.status()
    runner.assert_not_eq("FF8-not-terminal", st["state"], "failed")


async def test_ff9_status_safe(runner: R) -> None:
    feed, _ws = _mk_feed()
    blob = json.dumps(feed.status())
    runner.assert_not_in("FF9-no-token", "HSMSYNTH", blob)
    runner.assert_not_in("FF9-no-wss", "wss://", blob)


def test_ff10_hsm_key_extraction(runner: R) -> None:
    from brokers.fyers.feed import _hsm_key_from_access_token
    runner.assert_eq("FF10-bare-jwt",
                     _hsm_key_from_access_token(_jwt_with_hsm_key("K1")),
                     "K1")
    runner.assert_eq("FF10-prefixed-jwt",
                     _hsm_key_from_access_token(
                         "APP-100:" + _jwt_with_hsm_key("K2")),
                     "K2")


async def test_ff12_symbol_resolution_real_path(runner: R) -> None:
    """_resolve_hsm_symbols must build HSM tokens via the symbol-token API.

    Regression: the REST converter referenced an unimported USER_AGENT and
    died with NameError at first live login; tests had only ever pre-seeded
    the mapping. This drives the REAL code path with a stubbed HTTP layer.
    """
    import urllib.request

    from brokers.fyers.feed import FyersFeed

    calls = {}

    def fake_urlopen(req, timeout=0):
        calls["url"] = req.full_url
        calls["headers"] = dict(req.header_items())
        calls["body"] = json.loads(req.data.decode())

        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({
                    "s": "ok",
                    "validSymbol": {"NSE:SBIN-EQ": "10100000001234"},
                    "invalidSymbol": [],
                }).encode()

        return _R()

    real_urlopen = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        cfg = {"source_name": "fyers", "instrument_keys": ["NSE:SBIN-EQ"],
               "app_id": "APP-1",
               "access_token_getter": lambda: _jwt_with_hsm_key("HK"),
               "ws_connect": lambda token: asyncio.sleep(0, result=None),
               "utc_now_iso": lambda: ""}
        feed = FyersFeed(config=cfg, auth=object(), market_service=None)
        await feed._resolve_hsm_symbols(_jwt_with_hsm_key("HK"))
    finally:
        urllib.request.urlopen = real_urlopen

    runner.assert_in("FF12-endpoint", "/data/symbol-token", calls["url"])
    runner.assert_eq("FF12-body-symbols",
                     calls["body"]["symbols"], ["NSE:SBIN-EQ"])
    auth_headers = {k.lower(): v for k, v in calls["headers"].items()}
    runner.assert_true("FF12-auth-header-present",
                       any(v.startswith("eyJ") for v in auth_headers.values()))
    # NSE equity fytoken 1010... -> segment nse_cm, token = fytoken[10:]
    runner.assert_eq("FF12-hsm-token",
                     feed._hsm_symbols.get("NSE:SBIN-EQ"),
                     "sf|nse_cm|1234")
    runner.assert_eq("FF12-topic-inverse",
                     feed._hsm_by_topic.get("sf|nse_cm|1234"), "NSE:SBIN-EQ")


async def test_ff13_resolution_failure_retries(runner: R) -> None:
    """Failed symbol resolution must NOT cache identity tokens."""
    import urllib.request

    from brokers.fyers.feed import FyersFeed

    def failing_urlopen(req, timeout=0):
        raise OSError("network down")

    real = urllib.request.urlopen
    urllib.request.urlopen = failing_urlopen
    try:
        cfg = {"source_name": "fyers", "instrument_keys": ["NSE:X"],
               "app_id": "A",
               "access_token_getter": lambda: _jwt_with_hsm_key("HK"),
               "ws_connect": lambda token: asyncio.sleep(0, result=None),
               "utc_now_iso": lambda: ""}
        feed = FyersFeed(config=cfg, auth=object(), market_service=None)
        await feed._resolve_hsm_symbols(_jwt_with_hsm_key("HK"))
    finally:
        urllib.request.urlopen = real

    runner.assert_eq("FF13-no-identity-cache",
                     feed._hsm_symbols, {})


async def test_ff14_auth_failure_raises_auth_invalid(runner: R) -> None:
    """A 401/403 from the symbol-token API must raise _FyersAuthInvalid so the
    session transitions to auth_required ("Daily Login Required") instead of
    retrying forever on a stale/expired token after a MarketHub restart."""
    import urllib.error
    import urllib.request

    from brokers.fyers.feed import FyersFeed, _FyersAuthInvalid

    def auth_fail_urlopen(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", {}, None)

    real = urllib.request.urlopen
    urllib.request.urlopen = auth_fail_urlopen
    try:
        cfg = {"source_name": "fyers", "instrument_keys": ["NSE:X"],
               "app_id": "A",
               "access_token_getter": lambda: _jwt_with_hsm_key("HK"),
               "ws_connect": lambda token: asyncio.sleep(0, result=None),
               "utc_now_iso": lambda: ""}
        feed = FyersFeed(config=cfg, auth=object(), market_service=None)
        raised = None
        try:
            await feed._resolve_hsm_symbols(_jwt_with_hsm_key("HK"))
        except _FyersAuthInvalid:
            raised = True
        runner.assert_eq("FF14-raises-auth-invalid", raised, True)
    finally:
        urllib.request.urlopen = real


async def test_ff11_terminal_outcome_no_crash(runner: R) -> None:
    """_run_session returning the _TERMINAL sentinel must exit cleanly.

    Regression: the run loop used isinstance(outcome, _TERMINAL) against a
    sentinel INSTANCE, raising TypeError on every real connect - found
    during first live login.
    """
    from brokers.fyers.feed import _TERMINAL, FyersFeed

    cfg = {"source_name": "fyers", "instrument_keys": ["NSE:X"],
           "app_id": "A", "access_token_getter": lambda: "T",
           "ws_connect": lambda token: asyncio.sleep(0, result=None),
           "utc_now_iso": lambda: ""}
    feed = FyersFeed(config=cfg, auth=object(), market_service=None)

    async def _terminal_session(stop_event):
        feed._set_state("failed", reason="config")
        return _TERMINAL

    feed._run_session = _terminal_session

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.wait_for(task, timeout=3)

    await run()
    st = feed.status()
    runner.assert_eq("FF11-terminal-state", st["state"], "failed")
    runner.assert_in("FF11-terminal-exit", "terminal",
                     st.get("last_exit_reason") or "")



async def test_ff_tbt_status_reports_parent_owned_task(runner: R) -> None:
    # Parent-owned TBT task state must be reported accurately in nested status.
    from brokers.fyers.feed import FyersFeed

    cfg = {"source_name": "fyers", "instrument_keys": ["NSE:X"],
           "app_id": "A", "access_token_getter": lambda: "T",
           "ws_connect": lambda token: asyncio.sleep(0, result=None),
           "utc_now_iso": lambda: "", "tbt_enabled": True}
    feed = FyersFeed(config=cfg, auth=object(), market_service=None)

    class _LiveTask:
        def done(self):
            return False

    class _DoneTask:
        def done(self):
            return True

    class _FakeTbt:
        def status(self):
            return {"enabled": True, "state": "connected", "task_running": False}

    feed._tbt_feed = _FakeTbt()
    feed._tbt_task = _LiveTask()
    st = feed.status()["tbt"]
    runner.assert_eq("FF-TBT-task-running-live", st.get("task_running"), True)

    feed._tbt_task = _DoneTask()
    st = feed.status()["tbt"]
    runner.assert_eq("FF-TBT-task-running-done", st.get("task_running"), False)



class _FakeCatalog:
    def __init__(self, rows):
        self._rows = {r["provider_symbol"]: r for r in rows}

    def get_by_key(self, key):
        return self._rows.get(key)


async def test_ff_tbt_eligibility_filters_before_entitlement(runner: R) -> None:
    # Unsupported BSE index must not consume Standard slot; eligible NSE equity must be admitted.
    from brokers.fyers.feed import FyersFeed

    catalog = _FakeCatalog([
        {"provider_symbol": "BSE:BANKEX-INDEX", "segment": "BSE_INDEX", "instrument_type": "INDEX"},
        {"provider_symbol": "NSE:RELIANCE-EQ", "segment": "NSE_EQ", "instrument_type": "EQUITY"},
    ])
    cfg = {
        "source_name": "fyers",
        "instrument_keys": ["BSE:BANKEX-INDEX", "NSE:RELIANCE-EQ"],
        "instrument_keys_ordered": ["BSE:BANKEX-INDEX", "NSE:RELIANCE-EQ"],
        "app_id": "A",
        "access_token_getter": lambda: "T",
        "ws_connect": lambda token: asyncio.sleep(0, result=None),
        "utc_now_iso": lambda: "",
        "tbt_enabled": True,
        "instrument_catalog": catalog,
        "tbt_tier": "standard",
    }
    feed = FyersFeed(config=cfg, auth=object(), market_service=None)

    class _FakeTbt:
        def __init__(self):
            self.subscribed = []
            self.unsubscribed = []
            self._desired = set()
        async def subscribe(self, symbols):
            self.subscribed.append(list(symbols))
            self._desired.update(symbols)
        async def unsubscribe(self, symbols):
            self.unsubscribed.append(list(symbols))
            for s in symbols:
                self._desired.discard(s)
        @property
        def desired_symbols(self):
            return set(self._desired)
        def status(self):
            return {"enabled": True, "state": "connected", "task_running": False,
                    "subscribes": 1, "subscribed_count": 1, "snapshot_count": 0,
                    "delta_count": 0, "last_message_at": None, "safe_last_error": None}

    feed._tbt_feed = _FakeTbt()
    await feed._sync_tbt_desired()
    st = feed._tbt_status()
    runner.assert_eq("FF-TBT-admitted", st.get("admitted_symbols"), ["NSE:RELIANCE-EQ"])
    runner.assert_eq("FF-TBT-eligible-count", st.get("eligible_count"), 1)
    runner.assert_true("FF-TBT-ineligible-has-bse", any(
        r.get("symbol") == "BSE:BANKEX-INDEX" for r in (st.get("ineligible_reasons") or [])))
    runner.assert_eq("FF-TBT-rejected-entitlement", st.get("rejected_by_entitlement"), [])
    runner.assert_eq("FF-TBT-subscribed", feed._tbt_feed.subscribed[-1], ["NSE:RELIANCE-EQ"])


async def test_ff_tbt_explicit_symbols_not_silently_replaced(runner: R) -> None:
    # Explicit unsupported selection must not be silently replaced; eligible later selection admitted if within limit.
    from brokers.fyers.feed import FyersFeed

    catalog = _FakeCatalog([
        {"provider_symbol": "BSE:BANKEX-INDEX", "segment": "BSE_INDEX", "instrument_type": "INDEX"},
        {"provider_symbol": "NSE:RELIANCE-EQ", "segment": "NSE_EQ", "instrument_type": "EQUITY"},
    ])
    cfg = {
        "source_name": "fyers",
        "instrument_keys": ["BSE:BANKEX-INDEX", "NSE:RELIANCE-EQ"],
        "instrument_keys_ordered": ["BSE:BANKEX-INDEX", "NSE:RELIANCE-EQ"],
        "app_id": "A",
        "access_token_getter": lambda: "T",
        "ws_connect": lambda token: asyncio.sleep(0, result=None),
        "utc_now_iso": lambda: "",
        "tbt_enabled": True,
        "instrument_catalog": catalog,
        "tbt_tier": "standard",
        "tbt_symbols": ["BSE:BANKEX-INDEX", "NSE:RELIANCE-EQ"],
    }
    feed = FyersFeed(config=cfg, auth=object(), market_service=None)

    class _FakeTbt:
        def __init__(self):
            self.subscribed = []
            self._desired = set()
        async def subscribe(self, symbols):
            self.subscribed.append(list(symbols))
            self._desired.update(symbols)
        async def unsubscribe(self, symbols):
            for s in symbols:
                self._desired.discard(s)
        @property
        def desired_symbols(self):
            return set(self._desired)
        def status(self):
            return {"enabled": True, "state": "connected", "task_running": False,
                    "subscribes": 1, "subscribed_count": 1, "snapshot_count": 0,
                    "delta_count": 0, "last_message_at": None, "safe_last_error": None}

    feed._tbt_feed = _FakeTbt()
    await feed._sync_tbt_desired()
    st = feed._tbt_status()
    runner.assert_eq("FF-TBT-explicit-admitted", st.get("admitted_symbols"), ["NSE:RELIANCE-EQ"])
    runner.assert_true("FF-TBT-explicit-bse-ineligible", any(
        r.get("symbol") == "BSE:BANKEX-INDEX" for r in (st.get("ineligible_reasons") or [])))


async def test_ff_tbt_metadata_unavailable_fails_closed(runner: R) -> None:
    from brokers.fyers.feed import FyersFeed

    catalog = _FakeCatalog([])
    cfg = {
        "source_name": "fyers",
        "instrument_keys": ["NSE:RELIANCE-EQ"],
        "instrument_keys_ordered": ["NSE:RELIANCE-EQ"],
        "app_id": "A",
        "access_token_getter": lambda: "T",
        "ws_connect": lambda token: asyncio.sleep(0, result=None),
        "utc_now_iso": lambda: "",
        "tbt_enabled": True,
        "instrument_catalog": catalog,
        "tbt_tier": "standard",
    }
    feed = FyersFeed(config=cfg, auth=object(), market_service=None)

    class _FakeTbt:
        def __init__(self):
            self.subscribed = []
            self._desired = set()
        async def subscribe(self, symbols):
            self.subscribed.append(list(symbols))
            self._desired.update(symbols)
        async def unsubscribe(self, symbols):
            for s in symbols:
                self._desired.discard(s)
        @property
        def desired_symbols(self):
            return set(self._desired)
        def status(self):
            return {"enabled": True, "state": "connected", "task_running": False,
                    "subscribes": 0, "subscribed_count": 0, "snapshot_count": 0,
                    "delta_count": 0, "last_message_at": None, "safe_last_error": None}

    feed._tbt_feed = _FakeTbt()
    await feed._sync_tbt_desired()
    st = feed._tbt_status()
    runner.assert_eq("FF-TBT-no-meta-admitted", st.get("admitted_symbols"), [])
    runner.assert_true("FF-TBT-no-meta-reason", any(
        r.get("reason") == "instrument_metadata_unavailable" for r in (st.get("ineligible_reasons") or [])))



async def test_ff_tbt_eligibility_matrix_and_prime_limit(runner: R) -> None:
    from brokers.fyers.feed import FyersFeed

    catalog = _FakeCatalog([
        {"provider_symbol": "NSE:RELIANCE-EQ", "segment": "NSE_EQ", "instrument_type": "EQUITY"},
        {"provider_symbol": "NSE:NIFTY24NOVFUT", "segment": "NSE_FO", "instrument_type": "FUTURE"},
        {"provider_symbol": "NSE:NIFTY24NOV24000CE", "segment": "NSE_FO", "instrument_type": "OPTION"},
        {"provider_symbol": "NSE:NIFTY50-INDEX", "segment": "NSE_INDEX", "instrument_type": "INDEX"},
        {"provider_symbol": "BSE:RELIANCE-A", "segment": "BSE_EQ", "instrument_type": "EQUITY"},
        {"provider_symbol": "BSE:SENSEX24NOVFUT", "segment": "BSE_FO", "instrument_type": "FUTURE"},
        {"provider_symbol": "BSE:SENSEX24NOV80000CE", "segment": "BSE_FO", "instrument_type": "OPTION"},
        {"provider_symbol": "NSE:NIFTYBEES-EQ", "segment": "NSE_EQ", "instrument_type": "ETF"},
    ])
    keys = [r["provider_symbol"] for r in catalog._rows.values()]
    cfg = {
        "source_name": "fyers",
        "instrument_keys": keys,
        "instrument_keys_ordered": keys,
        "app_id": "A",
        "access_token_getter": lambda: "T",
        "ws_connect": lambda token: asyncio.sleep(0, result=None),
        "utc_now_iso": lambda: "",
        "tbt_enabled": True,
        "instrument_catalog": catalog,
        "tbt_tier": "prime",
    }
    feed = FyersFeed(config=cfg, auth=object(), market_service=None)

    class _FakeTbt:
        def __init__(self):
            self.subscribed = []
            self._desired = set()
        async def subscribe(self, symbols):
            self.subscribed.append(list(symbols))
            self._desired.update(symbols)
        async def unsubscribe(self, symbols):
            for s in symbols:
                self._desired.discard(s)
        @property
        def desired_symbols(self):
            return set(self._desired)
        def status(self):
            return {"enabled": True, "state": "connected", "task_running": False,
                    "subscribes": 1, "subscribed_count": 3, "snapshot_count": 0,
                    "delta_count": 0, "last_message_at": None, "safe_last_error": None}

    feed._tbt_feed = _FakeTbt()
    await feed._sync_tbt_desired()
    st = feed._tbt_status()
    # Eligible: NSE_EQ/EQUITY, NSE_FO/FUTURE, NSE_FO/OPTION only (ETF excluded unless explicitly supported)
    runner.assert_eq("FF-TBT-prime-admitted", st.get("admitted_symbols"),
                     ["NSE:RELIANCE-EQ", "NSE:NIFTY24NOVFUT", "NSE:NIFTY24NOV24000CE"])
    runner.assert_eq("FF-TBT-prime-eligible-count", st.get("eligible_count"), 3)
    reasons = {r.get("symbol"): r.get("reason") for r in (st.get("ineligible_reasons") or [])}
    runner.assert_true("FF-TBT-index-rejected", "NSE:NIFTY50-INDEX" in reasons)
    runner.assert_true("FF-TBT-bse-eq-rejected", "BSE:RELIANCE-A" in reasons)
    runner.assert_true("FF-TBT-bse-fo-fut-rejected", "BSE:SENSEX24NOVFUT" in reasons)
    runner.assert_true("FF-TBT-bse-fo-opt-rejected", "BSE:SENSEX24NOV80000CE" in reasons)
    runner.assert_true("FF-TBT-etf-rejected", "NSE:NIFTYBEES-EQ" in reasons)


# -- main -------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    await test_ff1_registry(runner)
    await test_ff2_factory_requires_getter(runner)
    test_ff10_hsm_key_extraction(runner)
    await test_ff12_symbol_resolution_real_path(runner)
    await test_ff13_resolution_failure_retries(runner)

    for coro_fn in (test_ff3_to_ff5_lifecycle, test_ff6_delta_frames,
                    test_ff7_clean_stop, test_ff8_transport_failure_reconnects,
                    test_ff9_status_safe, test_ff11_terminal_outcome_no_crash,
                    test_ff_tbt_status_reports_parent_owned_task,
                    test_ff_tbt_eligibility_filters_before_entitlement,
                    test_ff_tbt_explicit_symbols_not_silently_replaced,
                    test_ff_tbt_metadata_unavailable_fails_closed,
                    test_ff_tbt_eligibility_matrix_and_prime_limit):
        fn = getattr(sys.modules[__name__], coro_fn.__name__)
        await fn(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
