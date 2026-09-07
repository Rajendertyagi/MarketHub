"""Canonical instrument catalog: official-master sync + search.

Provider sources (official only):
  * Upstox: https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz
    (gzip JSON array; refreshed daily by Upstox)
  * Fyers:  https://public.fyers.in/sym_details/<SEGMENT>_sym_master.json

Sync is MANUAL (WebUI button / API call) — never automatic hammering.
Replacement is transactional per provider: a failed download leaves the
previous catalog intact.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import urllib.request
from typing import Any

logger = logging.getLogger("event_server")

UPSTOX_MASTER_URL = ("https://assets.upstox.com/market-quote/"
                     "instruments/exchange/complete.json.gz")
FYERS_SEGMENT_URLS = {
    "NSE_CM": "https://public.fyers.in/sym_details/NSE_CM_sym_master.json",
    "NSE_FO": "https://public.fyers.in/sym_details/NSE_FO_sym_master.json",
    "BSE_CM": "https://public.fyers.in/sym_details/BSE_CM_sym_master.json",
    "MCX_COM": "https://public.fyers.in/sym_details/MCX_COM_sym_master.json",
}
USER_AGENT = "MarketHub/1.0 (trading-terminal)"
FETCH_TIMEOUT_S = 60


class InstrumentSyncError(RuntimeError):
    """Instrument-master download/parse failure (safe to surface)."""


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
            return resp.read()
    except Exception as exc:
        raise InstrumentSyncError(
            f"instrument master download failed: {type(exc).__name__}"
        ) from exc


# ---------------------------------------------------------------------------
# Upstox master parsing
# ---------------------------------------------------------------------------


def _opt_str(v: Any) -> str | None:
    if v in (None, "", "0", "-"):
        return None
    return str(v)


def _opt_float(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _opt_int(v: Any) -> int | None:
    f = _opt_float(v)
    return int(f) if f is not None else None


def _upstox_expiry_iso(raw: Any) -> str | None:
    """Upstox master expiry (epoch MILLISECONDS, end-of-day) -> ISO date.

    Same canonical YYYY-MM-DD convention as the Fyers parser so the
    subscription resolver's `expiry >= today` comparison works across
    providers. Verified against the live master: 1790706599000 ->
    2026-09-29 ("NIFTY FUT 29 SEP 26").
    """
    try:
        if raw in (None, "", 0):
            return None
        from datetime import datetime, timezone
        return datetime.fromtimestamp(
            int(raw) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


# Upstox instrument_type -> canonical type. Verified against the live
# complete.json.gz master (Sept 2026): NSE_FO rows carry "FUT" for futures
# and "CE"/"PE" directly for options (there is NO separate option_type
# field); NSE_EQ rows carry "EQ"; index segments carry "INDEX".
_UPSTOX_INST_TYPE = {
    "EQ": "EQUITY",
    "INDEX": "INDEX",
    "FUT": "FUTURE",
    "FUTIDX": "FUTURE",
    "FUTSTK": "FUTURE",
    "CE": "OPTION",
    "PE": "OPTION",
    "OPTIDX": "OPTION",
    "OPTSTK": "OPTION",
}


def upstox_master_records(payload: bytes | list) -> list[dict[str, Any]]:
    """Parse the official Upstox complete.json(.gz) into canonical records.

    Real master schema (verified against the live download, Sept 2026):
        instrument_key   "NSE_FO|42631" / "NSE_EQ|INE002A01018"   (feed key)
        exchange         base exchange: "NSE" / "BSE" / "MCX"
        segment          "NSE_FO" / "NSE_EQ" / "NSE_INDEX" / ...
        instrument_type  "EQ" | "INDEX" | "FUT" | "CE" | "PE"
        trading_symbol   "NIFTY 23800 CE 08 SEP 26"
        underlying_symbol "NIFTY" / "RELIANCE" (structured — no guessing)
        asset_key        "NSE_INDEX|Nifty 50" (canonical underlying link)
        expiry           epoch MILLISECONDS (end-of-day)
        strike_price     numeric (options only)
        lot_size / minimum_lot / tick_size / isin / name / exchange_token

    ``instrument_token`` stores the provider instrument_key — that is the
    identity the Upstox feed/REST accepts for subscriptions.
    """
    if isinstance(payload, bytes):
        if payload[:2] == b"\x1f\x8b":
            payload = gzip.decompress(payload)
        data = json.loads(payload.decode("utf-8"))
    else:
        data = payload
    if not isinstance(data, list):
        raise InstrumentSyncError("upstox master: expected JSON array")
    records: list[dict[str, Any]] = []
    for e in data:
        if not isinstance(e, dict):
            continue
        raw_type = _opt_str(e.get("instrument_type"))
        inst_type = _UPSTOX_INST_TYPE.get((raw_type or "").upper())
        option_type = raw_type if raw_type in ("CE", "PE") else None
        instrument_key = _opt_str(e.get("instrument_key"))
        records.append({
            "instrument_token": instrument_key,
            "exchange": _opt_str(e.get("exchange")),
            "segment": _opt_str(e.get("segment")),
            "tradingsymbol": _opt_str(e.get("trading_symbol")),
            "name": e.get("name") or None,
            "instrument_type": inst_type,
            "expiry": _upstox_expiry_iso(e.get("expiry")),
            "strike": _opt_float(e.get("strike_price")),
            "option_type": option_type,
            "lot_size": _opt_int(e.get("lot_size")
                                 or e.get("minimum_lot")),
            "tick_size": _opt_float(e.get("tick_size")),
            "isin": _opt_str(e.get("isin")),
            "underlying": _opt_str(e.get("underlying_symbol")),
            "provider_symbol": instrument_key,
        })
    return [r for r in records if r["instrument_token"]
            and r["exchange"] and r["tradingsymbol"]]


# ---------------------------------------------------------------------------
# Fyers master parsing
# ---------------------------------------------------------------------------

# Fyers sym_master files are JSON OBJECTS keyed by "EXCH:SYMBOL" with
# authoritative per-instrument fields (verified against live masters):
#   fyToken, exchange (10 NSE/12 BSE/11 MCX), segment (10 CM/11 FO),
#   exInstType (0 EQ, 9 ETF, 10 INDEX, 11 FUTIDX, 13 FUTSTK,
#   14 OPTIDX, 15 OPTSTK), expiryDate (epoch seconds string),
#   optType (CE/PE/XX), strikePrice, minLotSize, tickSize, isin,
#   underSym, symDetails.
_FYERS_INST_TYPE = {
    0: "EQUITY", 9: "ETF", 10: "INDEX",
    11: "FUTURE", 13: "FUTURE",
    14: "OPTION", 15: "OPTION",
}
_FYERS_EXCHANGE = {10: "NSE", 12: "BSE", 11: "MCX"}


def _fyers_expiry_iso(raw: Any) -> str | None:
    """epoch-seconds string -> ISO date (YYYY-MM-DD)."""
    try:
        if raw in (None, "", 0):
            return None
        from datetime import datetime, timezone
        return datetime.fromtimestamp(
            int(raw), tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


def fyers_master_records(payload: bytes) -> list[dict[str, Any]]:
    """Parse official Fyers <SEGMENT>_sym_master.json into canonical records."""
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise InstrumentSyncError(
            "fyers master: expected JSON object keyed by EXCH:SYMBOL")
    records: list[dict[str, Any]] = []
    for symbol_key, row in data.items():
        if not isinstance(row, dict):
            continue
        inst_type = _FYERS_INST_TYPE.get(
            row.get("exInstType"))
        rec: dict[str, Any] = {
            "provider": "fyers",
            "provider_symbol": symbol_key,
            "instrument_token": _opt_str(row.get("fyToken")),
            "exchange": (_FYERS_EXCHANGE.get(row.get("exchange"))
                         or _opt_str(row.get("exchangeName"))
                         or _opt_str(row.get("exchange"))),
            "segment": _opt_str(row.get("segment")),
            "name": row.get("symDetails") or row.get("symbolDetails"),
            "tradingsymbol": (_opt_str(row.get("symTicker"))
                              or symbol_key),
            "instrument_type": inst_type,
            "expiry": _fyers_expiry_iso(row.get("expiryDate")),
            "strike": _opt_float(row.get("strikePrice")),
            "option_type": ((_opt_str(row.get("optType")) or "").upper()
                            or None) if inst_type == "OPTION" else None,
            "lot_size": _opt_int(row.get("minLotSize")),
            "tick_size": _opt_float(row.get("tickSize")),
            "isin": _opt_str(row.get("isin")),
            "underlying": _opt_str(row.get("underSym")),
        }
        if rec["option_type"] in ("XX", ""):
            rec["option_type"] = None
        if rec["instrument_token"] and rec["exchange"] \
                and rec["tradingsymbol"]:
            records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Catalog service
# ---------------------------------------------------------------------------


class InstrumentCatalog:
    """Sync + search over the canonical instruments table."""

    def __init__(self, event_store: Any) -> None:
        self._store = event_store

    def sync_upstox(self, *, fetch=None) -> dict[str, Any]:
        fetch = fetch or _fetch
        raw = fetch(UPSTOX_MASTER_URL)
        records = upstox_master_records(raw)
        inserted = self._store.replace_provider_instruments("upstox",
                                                            records)
        logger.info("upstox instrument sync: %d records", inserted)
        return {"provider": "upstox", "records": inserted,
                "parsed": len(records)}

    def sync_fyers(self, *, fetch=None) -> dict[str, Any]:
        fetch = fetch or _fetch
        # Fyers publishes one master PER SEGMENT but the catalog replaces
        # per PROVIDER — accumulate every segment first, then replace once,
        # otherwise each segment would wipe the previous one.
        all_records: list[dict[str, Any]] = []
        parsed = 0
        for url in FYERS_SEGMENT_URLS.values():
            try:
                records = fyers_master_records(fetch(url))
            except InstrumentSyncError as exc:
                logger.warning("fyers segment sync skipped (%s): %s",
                               url.rsplit("/", 1)[-1], exc)
                continue
            parsed += len(records)
            all_records.extend(records)
        total = self._store.replace_provider_instruments("fyers",
                                                         all_records)
        logger.info("fyers instrument sync: %d records", total)
        return {"provider": "fyers", "records": total, "parsed": parsed}

    def search(self, **kw: Any) -> list[dict[str, Any]]:
        return self._store.search_instruments(**kw)

    def derivative_expiries(self, underlying: str,
                            instrument_type: str) -> list[str]:
        return self._store.derivative_expiries(underlying, instrument_type)

    def option_strikes(self, underlying: str, expiry: str) -> list[dict]:
        return self._store.option_strikes(underlying, expiry)

    def get(self, provider: str, token: str) -> dict[str, Any] | None:
        return self._store.get_instrument(provider, token)

    def sync_state(self) -> list[dict[str, Any]]:
        return self._store.instruments_sync_state()
