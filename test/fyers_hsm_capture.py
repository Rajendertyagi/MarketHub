#!/usr/bin/env python3
"""Live FYERS HSM capture harness (read-only).

Captures the RAW bytes of a FYERS data-socket snapshot and the
INDEPENDENT reference quote for the same instrument, so the decoder can
be validated against a recorded ground truth instead of a plausible
number.

What it does
-------------
1. reads the stored FYERS credential (never printed);
2. resolves the API symbol to its HSM topic through the official
   symbol-token endpoint + the official SDK ``map.json`` index map;
3. connects to the HSM data socket with the official handshake
   (auth frame -> mode frame -> subscribe frame) and records the raw
   bytes of the first datafeed frame;
4. fetches the reference quote for the SAME symbol through the FYERS
   REST Quotes API - a different API family and transport from the
   socket - and records its value, previous close and timestamp;
5. writes a JSON fixture (raw bytes + metadata + reference) that
   ``test/test_fyers_decoder.py`` replays as a regression.

Usage:
    python test/fyers_hsm_capture.py --symbol NSE:NIFTY50-INDEX
    python test/fyers_hsm_capture.py --symbol NSE:SBIN-EQ
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import pathlib
import sys
from typing import Any

_PROJECT_DIR = pathlib.Path(__file__).resolve().parent.parent
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

from brokers.fyers.feed import (
    _hsm_auth_frame,
    _hsm_key_from_access_token,
    _hsm_mode_frame,
    _hsm_subscribe_frame,
)
from brokers.fyers.hsm_symbols import (
    INDEX_TOPICS,
    resolve_wire_symbols,
)

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"


def _credential() -> str:
    """The stored FYERS credential - never printed.

    The production database is COPIED into a temporary directory first:
    the capture must never open (and therefore never checkpoint or
    modify) the live ``data/events.db``.
    """
    import shutil
    import tempfile

    from app.paths import DATA_ROOT
    from app.secrets_store import CredentialStore
    from core.persistence.store import EventStore

    tmp = tempfile.mkdtemp(prefix="fyers-capture-")
    shutil.copy2(pathlib.Path(DATA_ROOT) / "events.db",
                 pathlib.Path(tmp) / "events.db")
    shutil.copy2(pathlib.Path(DATA_ROOT) / "master.key",
                 pathlib.Path(tmp) / "master.key")
    store = EventStore(str(pathlib.Path(tmp) / "events.db"))
    cs = CredentialStore(store, data_dir=pathlib.Path(tmp))
    stored = cs.load_fyers_access_token()
    if isinstance(stored, dict):
        token = str(stored.get("access_token") or "")
        expires = stored.get("expires_at")
    else:
        token = str(stored or "")
        expires = None
    if not token:
        raise SystemExit("no stored FYERS session")
    if expires:
        print(f"session expires at {expires} (never printed)")
    return token


async def _capture(topic: str, seconds: float) -> dict[str, Any] | None:
    """Connect, subscribe and return the first raw datafeed frame."""
    import websockets

    url = "wss://socket.fyers.in/hsm/v1-5/prod"
    deadline = asyncio.get_event_loop().time() + seconds
    async with websockets.connect(url, close_timeout=2) as ws:
        await ws.send(_hsm_auth_frame(_hsm_key_from_access_token(_credential())))
        raw = await asyncio.wait_for(ws.recv(), timeout=15)
        if not (isinstance(raw, (bytes, bytearray)) and len(raw) > 7
                and raw[7:8] == b"K"):
            raise SystemExit("HSM auth was not accepted")
        await ws.send(_hsm_mode_frame())
        await ws.send(_hsm_subscribe_frame([topic]))
        while asyncio.get_event_loop().time() < deadline:
            try:
                frame = await asyncio.wait_for(ws.recv(), timeout=5)
            except asyncio.TimeoutError:
                continue
            if isinstance(frame, (bytes, bytearray)) and len(frame) > 2 \
                    and frame[2] == 6:
                return {
                    "topic": topic,
                    "raw_hex": bytes(frame).hex(),
                    "raw_len": len(frame),
                    "captured_at": datetime.datetime.now(
                        datetime.timezone.utc).isoformat(timespec="seconds"),
                }
    return None


def _rest_reference(symbol: str) -> dict[str, Any]:
    """The reference quote from the FYERS REST Quotes API."""
    import urllib.parse
    import urllib.request

    token = _credential()
    bare = token.split(":", 1)[1] if ":" in token else token
    url = ("https://api-t1.fyers.in/data/quotes?symbols="
           + urllib.parse.quote(symbol, safe=",:"))
    req = urllib.request.Request(url, headers={
        "Authorization": bare, "Content-Type": "application/json",
        "User-Agent": "MarketHub/1.0 (trading-terminal)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    quotes = payload.get("d") or payload.get("data") or []
    first = quotes[0] if quotes else {}
    values = first.get("v") or {}
    return {
        "source": "FYERS REST GET /data/quotes",
        "symbol": first.get("n") or symbol,
        "ltp": values.get("lp"),
        "prev_close": values.get("prev_close_price"),
        "exch_feed_time": values.get("exch_feed_time"),
        "fetched_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="NSE:NIFTY50-INDEX")
    parser.add_argument("--seconds", type=float, default=45.0)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    symbol = args.symbol
    token = _credential()
    resolution = resolve_wire_symbols([symbol], token)
    topic = resolution.resolved.get(symbol)
    if not topic:
        # An index without an official SDK topic is not streamable; the
        # reference quote is still recorded (REST path).
        print(f"{symbol}: no official HSM topic "
              f"({resolution.unresolved.get(symbol)})")
        topic = None
    else:
        print(f"{symbol}: HSM topic {topic}")

    captured = None
    if topic:
        captured = asyncio.run(_capture(topic, args.seconds))
        if captured is None:
            print(f"{symbol}: no datafeed frame received within "
                  f"{args.seconds}s")
    reference = _rest_reference(symbol)
    print(f"{symbol}: reference ltp={reference['ltp']} "
          f"prev_close={reference['prev_close']} "
          f"exch_feed_time={reference['exch_feed_time']}")

    record = {
        "captured_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "symbol": symbol,
        "hsm_topic": topic,
        "index_topic_map_entry": INDEX_TOPICS.get(symbol),
        "frame": captured,
        "reference": reference,
        "provider": "fyers",
    }
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    out = pathlib.Path(args.out) if args.out else (
        FIXTURE_DIR / f"fyers_hsm_capture_{symbol.replace(':', '_')}.json")
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print("written:", out)
    return 0 if captured else 2


if __name__ == "__main__":
    raise SystemExit(main())
