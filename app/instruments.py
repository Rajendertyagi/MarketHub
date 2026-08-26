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


def upstox_master_records(payload: bytes | list) -> list[dict[str, Any]]:
    """Parse the official Upstox complete.json(.gz) into canonical records."""
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
        records.append({
            "instrument_token": _opt_str(e.get("instrument_token")),
            "exchange": e.get("exchange"),
            "tradingsymbol": e.get("tradingsymbol"),
            "name": e.get("name") or None,
            "instrument_type": _opt_str(e.get("instrument_type")),
            "segment": _opt_str(e.get("segment")),
            "expiry": _opt_str(e.get("expiry")),
            "strike": _opt_float(e.get("strike")),
            "option_type": _opt_str(e.get("option_type")),
            "lot_size": _opt_int(e.get("lot_size")),
            "tick_size": _opt_float(e.get("tick_size")),
            "isin": _opt_str(e.get("isin")),
            "underlying": _opt_str(e.get("underlying_symbol")
                                   or e.get("underlying")),
            "provider_symbol": None,
        })
    return [r for r in records if r["instrument_token"]
            and r["exchange"] and r["tradingsymbol"]]


# ---------------------------------------------------------------------------
# Fyers master parsing
# ---------------------------------------------------------------------------

# Fyers sym_master JSON rows use positional arrays behind a header object;
# documented key order for NSE_CM/NSE_FO:
#   0 fyToken, 1 symbol string, 2 exchange, 3 segment, 4 symbolDetails,
#   5 exSymName, 6 displayName?, ... variable — parse defensively by name
# when dicts, positionally when lists with >=13 columns:
#   [fyToken, sym, exch, seg, details, exSym, tick, lot, currency?, ...]
_FYERS_POS = {"token": 0, "symbol": 1, "exchange": 2, "segment": 3,
              "details": 4, "ex_sym": 5, "tick": 6, "lot": 7}


def fyers_master_records(payload: bytes) -> list[dict[str, Any]]:
    """Parse official Fyers <SEGMENT>_sym_master.json into canonical records."""
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, list):
        raise InstrumentSyncError("fyers master: expected JSON array")
    records: list[dict[str, Any]] = []
    for row in data:
        rec: dict[str, Any] = {}
        if isinstance(row, dict):
            rec["provider_symbol"] = row.get("symbol") or row.get("Sym")
            rec["instrument_token"] = _opt_str(
                row.get("fyToken") or row.get("FytokenId"))
            rec["exchange"] = row.get("exchange") or row.get("Exch")
            rec["tradingsymbol"] = (row.get("symName")
                                    or row.get("Symbol") or "")
            rec["name"] = row.get("symbolDetails") or row.get("displayName")
            rec["instrument_type"] = _opt_str(row.get("optType")
                                              or row.get("InstrType"))
            rec["segment"] = _opt_str(row.get("segment") or row.get("Seg"))
            rec["expiry"] = _opt_str(row.get("expiryDate"))
            rec["strike"] = _opt_float(row.get("strikePrice"))
            rec["option_type"] = _opt_str(row.get("optType"))
            rec["lot_size"] = _opt_int(row.get("minLotSize")
                                       or row.get("LotSize"))
            rec["tick_size"] = _opt_float(row.get("tickSize"))
        elif isinstance(row, list) and len(row) > max(_FYERS_POS.values()):
            rec["provider_symbol"] = _opt_str(row[_FYERS_POS["symbol"]])
            rec["instrument_token"] = _opt_str(row[_FYERS_POS["token"]])
            rec["exchange"] = row[_FYERS_POS["exchange"]]
            rec["segment"] = _opt_str(row[_FYERS_POS["segment"]])
            rec["name"] = row[_FYERS_POS["details"]]
            rec["tradingsymbol"] = (_opt_str(row[_FYERS_POS["ex_sym"]])
                                    or rec["provider_symbol"] or "")
            rec["tick_size"] = _opt_float(row[_FYERS_POS["tick"]])
            rec["lot_size"] = _opt_int(row[_FYERS_POS["lot"]])
        else:
            continue
        if rec["instrument_token"] and rec["exchange"] and rec["tradingsymbol"]:
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
        total = 0
        parsed = 0
        for url in FYERS_SEGMENT_URLS.values():
            try:
                records = fyers_master_records(fetch(url))
            except InstrumentSyncError as exc:
                logger.warning("fyers segment sync skipped (%s): %s",
                               url.rsplit("/", 1)[-1], exc)
                continue
            parsed += len(records)
            total += self._store.replace_provider_instruments("fyers",
                                                              records)
        logger.info("fyers instrument sync: %d records", total)
        return {"provider": "fyers", "records": total, "parsed": parsed}

    def search(self, **kw: Any) -> list[dict[str, Any]]:
        return self._store.search_instruments(**kw)

    def get(self, provider: str, token: str) -> dict[str, Any] | None:
        return self._store.get_instrument(provider, token)

    def sync_state(self) -> list[dict[str, Any]]:
        return self._store.instruments_sync_state()
