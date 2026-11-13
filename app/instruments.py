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
import json
import logging
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

from app.catalog_sync import (
    FYERS_MASTER_SEGMENTS_MAP,
    MasterValidationError,
    WriteScope,
)
from core.persistence.modules.products import (
    KNOWN_SEGMENTS as _KNOWN_SEGMENTS,
)
from core.persistence.modules.products import (
    canonical_segment,
    canonicalize_record,
)

logger = logging.getLogger("event_server")

UPSTOX_MASTER_URL = ("https://assets.upstox.com/market-quote/"
                     "instruments/exchange/complete.json.gz")
# FYERS publishes one master PER SEGMENT:
#   https://public.fyers.in/sym_details/<MASTER>_sym_master.json
# The covered masters are exactly those that can contribute in-scope
# contracts (NSE F&O universe + the BSE/SENSEX leg). MCX commodities
# (MCX_COM) are out of scope; BSE_FO is REQUIRED for the SENSEX leg.
FYERS_SEGMENT_URLS = {
    "NSE_CM": "https://public.fyers.in/sym_details/NSE_CM_sym_master.json",
    "NSE_FO": "https://public.fyers.in/sym_details/NSE_FO_sym_master.json",
    "BSE_CM": "https://public.fyers.in/sym_details/BSE_CM_sym_master.json",
    "BSE_FO": "https://public.fyers.in/sym_details/BSE_FO_sym_master.json",
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
    return [canonicalize_record(r) for r in records
            if r["instrument_token"] and r["exchange"] and r["tradingsymbol"]]


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


def _validate_sensex_source_row(
    row: dict[str, Any], inst_type: str | None, symbol_key: str,
) -> None:
    """Reject a SENSEX row that lacks authoritative contract metadata.

    The provider master is the only source of truth for these contracts:
    a row that cannot be fully identified must abort the master (the
    previous catalogue is retained) instead of being silently skipped.
    """
    problems: list[str] = []
    if not row.get("fyToken"):
        problems.append("fyToken missing")
    if row.get("exchange") != 12:
        problems.append("exchange is not BSE")
    if not row.get("symTicker"):
        problems.append("symTicker missing")
    if row.get("tradeStatus") not in (0, 1):
        problems.append("tradeStatus missing/invalid")
    if inst_type not in ("FUTURE", "OPTION"):
        problems.append("exInstType is not a derivative type")
    if inst_type in ("FUTURE", "OPTION") and not row.get("expiryDate"):
        problems.append("expiryDate missing")
    if inst_type == "OPTION":
        try:
            strike = float(row.get("strikePrice"))
        except (TypeError, ValueError):
            strike = 0.0
        if strike <= 0:
            problems.append("strikePrice invalid")
        if str(row.get("optType") or "").upper() not in ("CE", "PE"):
            problems.append("optType is not CE/PE")
    if problems:
        raise InstrumentSyncError(
            "fyers master: malformed SENSEX record "
            f"{symbol_key}: {', '.join(problems)}")


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
        underlying = _opt_str(row.get("underSym"))
        if str(underlying or "").strip().upper() in SENSEX_UNDERLYINGS:
            _validate_sensex_source_row(row, inst_type, symbol_key)
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
            "underlying": underlying,
            # Transient provider metadata (not persisted): the selection
            # policy uses the provider's own trade status.
            "active": row.get("tradeStatus") != 0,
        }
        if rec["option_type"] in ("XX", ""):
            rec["option_type"] = None
        if rec["instrument_token"] and rec["exchange"] \
                and rec["tradingsymbol"]:
            records.append(canonicalize_record(rec))
    return records


# ---------------------------------------------------------------------------
# Catalog service
# ---------------------------------------------------------------------------

# MarketHub catalog segments. The minimal set the current product needs
# (equities, stock/index futures+options, the 8 canonical indices).
# Preferences persist in source_state; the master filter runs BEFORE the
# transactional catalog replace, so disabled segments leave the catalog
# entirely on the next successful sync.
DEFAULT_SEGMENTS: frozenset[str] = frozenset({
    "NSE_EQ", "NSE_FO", "NSE_INDEX", "BSE_INDEX", "BSE_FO",
})

# Known provider segments. GET /segments reports these plus anything newly
# discovered in a master; never hard-coded in the UI. The vocabulary itself is
# owned by the canonical catalog layer (core.persistence.modules.products).
# (Re-exported as _KNOWN_SEGMENTS for existing callers.)


def _derive_segment(row: dict[str, Any]) -> str | None:
    """Canonical MarketHub segment for a parsed record (provider-agnostic).

    Delegates to the single shared rule in the canonical catalog layer:
    Upstox rows carry a native canonical ``segment``; Fyers rows carry raw
    master codes (10/11/20) + exchange + instrument_type, so the segment is
    DERIVED per row — this keeps index rows (which live inside Fyers' cash
    masters) separable from equities without ever conflating the two.
    """
    return canonical_segment(row.get("exchange"), row.get("instrument_type"),
                             row.get("segment"))


#: Underlyings MarketHub requires from the BSE F&O universe.
#: Only Sensex and its relevant BSE derivatives are in scope; BANKEX,
#: SENSEX50, individual BSE stocks and unrelated BSE derivatives are
#: excluded. This is the single authoritative selection policy, applied
#: to every provider's BSE_FO master through its own metadata.
SENSEX_UNDERLYINGS: frozenset[str] = frozenset({"SENSEX"})


def _underlying_of(record: dict[str, Any]) -> str | None:
    """The provider's authoritative underlying for a catalogue record.

    FYERS publishes ``underSym``; Upstox publishes ``underlying_symbol``.
    Both are normalized into ``underlying`` at ingestion. There is NO
    symbol-shape fallback: an identifier that cannot be attributed through
    authoritative metadata is out of scope, never guessed.
    """
    raw = record.get("underlying")
    if raw is None:
        return None
    value = str(raw).strip().upper()
    return value or None


def _is_sensex_bse_fo(record: dict[str, Any]) -> bool:
    return (_derive_segment(record) == "BSE_FO"
            and _underlying_of(record) in SENSEX_UNDERLYINGS)


def _filter_sensex_bse_fo(
    records: list[dict[str, Any]], segments: frozenset[str] | set[str],
) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    """Filter records to enabled segments and in-scope BSE_FO contracts.

    Inactive instruments are excluded when the provider marks them
    (``active`` is False). Inside BSE_FO only authoritative-Sensex contracts
    are kept, enforcing the supported BSE F&O product scope consistently.
    """
    kept: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    filtered = 0
    for r in records:
        seg = _derive_segment(r)
        if seg is None or seg not in segments:
            filtered += 1
            continue
        if not r.get("active", True):
            filtered += 1
            continue
        if seg == "BSE_FO" and _underlying_of(r) not in SENSEX_UNDERLYINGS:
            filtered += 1
            continue
        kept.append(r)
        counts[seg] = counts.get(seg, 0) + 1
    return kept, counts, filtered


def _sensex_scope_report(
    records: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Selection evidence for one BSE_FO master (pre-write)."""
    bse_fo = [r for r in records if _derive_segment(r) == "BSE_FO"]
    matched = [r for r in bse_fo if _underlying_of(r) in SENSEX_UNDERLYINGS]
    active = [r for r in matched if r.get("active", True)]
    options = [r for r in active
               if r.get("instrument_type") == "OPTION"]
    excluded: dict[str, int] = {}
    for r in bse_fo:
        und = _underlying_of(r)
        if und in SENSEX_UNDERLYINGS:
            continue
        key = und or "<missing>"
        excluded[key] = excluded.get(key, 0) + 1
    return {
        "source_records": len(records),
        "bse_fo_records": len(bse_fo),
        "matched_sensex": len(matched),
        "active": len(active),
        "inactive": len(matched) - len(active),
        "futures": sum(1 for r in active
                       if r.get("instrument_type") == "FUTURE"),
        "options": len(options),
        "calls": sum(1 for r in options if r.get("option_type") == "CE"),
        "puts": sum(1 for r in options if r.get("option_type") == "PE"),
        "expiries": sorted({str(r["expiry"]) for r in active
                            if r.get("expiry")}),
        "excluded_bse_fo": sum(excluded.values()),
        "excluded_by_underlying": dict(sorted(excluded.items())),
        "persisted": 0,
    }


def _validate_bse_sensex_policy(
    master: str, records: Sequence[dict[str, Any]],
    *, enabled_segments: set[str] | None = None,
) -> None:
    """Structural invariants for BSE_FO SENSEX contracts in a master.

    A truncated or mis-parsed master must retain the previous catalogue,
    never replace it with a partial Sensex set. Invariants are contractual
    (authoritative fields and F&O shape), not an arbitrary shrinkage %.
    """
    if master == "BSE_FO":
        pass
    elif master == "complete":
        if enabled_segments is not None and "BSE_FO" not in enabled_segments:
            return
    else:
        return
    active = [r for r in records
              if _is_sensex_bse_fo(r) and r.get("active", True)]
    if not active:
        raise MasterValidationError(
            "BSE_FO master has no active SENSEX contracts")
    futures = [r for r in active
               if r.get("instrument_type") == "FUTURE"]
    calls = [r for r in active
             if r.get("instrument_type") == "OPTION"
             and r.get("option_type") == "CE"]
    puts = [r for r in active
            if r.get("instrument_type") == "OPTION"
            and r.get("option_type") == "PE"]
    if not futures:
        raise MasterValidationError(
            "BSE_FO master has no active SENSEX futures")
    if not calls or not puts:
        raise MasterValidationError(
            "BSE_FO master is missing SENSEX calls or puts")
    tokens = [str(r.get("instrument_token") or "") for r in active]
    if len(set(tokens)) != len(tokens):
        raise MasterValidationError(
            "BSE_FO master has duplicate SENSEX provider tokens")
    symbols = [str(r.get("tradingsymbol") or "") for r in active]
    if len(set(symbols)) != len(symbols):
        raise MasterValidationError(
            "BSE_FO master has duplicate SENSEX trading symbols")
    for r in active:
        itype = r.get("instrument_type")
        if not r.get("expiry"):
            raise MasterValidationError(
                f"BSE_FO SENSEX contract {r.get('tradingsymbol')} "
                "has no expiry")
        if itype == "OPTION":
            strike = r.get("strike")
            if strike is None or float(strike) <= 0:
                raise MasterValidationError(
                    f"BSE_FO SENSEX option {r.get('tradingsymbol')} "
                    "has no valid strike")
            if r.get("option_type") not in ("CE", "PE"):
                raise MasterValidationError(
                    f"BSE_FO SENSEX option {r.get('tradingsymbol')} "
                    "has no valid option type")
        elif itype != "FUTURE":
            raise MasterValidationError(
                f"BSE_FO SENSEX row {r.get('tradingsymbol')} "
                f"has unsupported type {itype!r}")


def _validate_fyers_master_policy(
    master: str, records: Sequence[dict[str, Any]],
) -> None:
    _validate_bse_sensex_policy(master, records)


def _sensex_master_report(
    master: str, records: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    if master not in ("BSE_FO", "complete"):
        return None
    return _sensex_scope_report(records)


#: Selective write scope: BSE_FO may only replace Sensex rows.
BSE_SENSEX_WRITE_SCOPE = WriteScope(segment="BSE_FO", column="underlying",
                                    values=("SENSEX",))
FYERS_WRITE_SCOPES: dict[str, WriteScope] = {
    "BSE_FO": BSE_SENSEX_WRITE_SCOPE,
}
UPSTOX_WRITE_SCOPES: dict[str, WriteScope] = {
    "BSE_FO": BSE_SENSEX_WRITE_SCOPE,
}


class InstrumentCatalog:
    """Sync + search over the canonical instruments table."""

    def __init__(self, event_store: Any) -> None:
        self._store = event_store
        # Revision of the catalogue, watched by the derived read models
        # (market.derivatives_universe). It advances on every successful
        # synchronisation - including the segment-scoped ones, which do
        # not go through the event store's provider-wide replace.
        self._revision = 0
        # Optional listener invoked after a sync that actually changed the
        # catalogue. The composition root registers the identity
        # registries so canonical identity is refreshed (and derived
        # read-model caches invalidated) as soon as a sync lands - not
        # only at process start.
        self._sync_listeners: list[Callable[[Any], None]] = []

    def add_sync_listener(self, listener: Callable[[Any], None]) -> None:
        """Register a callback invoked after a successful catalogue sync."""
        if listener is not None and listener not in self._sync_listeners:
            self._sync_listeners.append(listener)

    def _notify_sync(self, result: Any) -> None:
        """Notify listeners when the catalogue actually changed."""
        if getattr(result, "aborted", False) or not getattr(result, "records", 0):
            return
        # The catalogue changed -> advance the revision the derived read
        # models watch.
        self._revision += 1
        for listener in list(self._sync_listeners):
            try:
                listener(result)
            except Exception:
                logger.warning("catalogue sync listener failed", exc_info=True)

    # -- segment preferences (source_state: generic durable KV) ------------

    def get_enabled_segments(self) -> set[str]:
        """Persisted enabled segments, or the minimal default set.

        Never overwrites an existing user choice; the default applies
        only when no preference has ever been saved.
        """
        raw = self._store.get_source_state("instruments", "segments")
        if raw:
            try:
                import json as _json
                parsed = _json.loads(raw)
                if isinstance(parsed, list) and parsed:
                    return {str(s).upper() for s in parsed}
            except Exception:
                logger.warning("invalid saved segment preference; "
                               "using defaults")
        return set(DEFAULT_SEGMENTS)

    def set_enabled_segments(self, segments: list[str]) -> set[str]:
        """Persist the enabled segment list (idempotent)."""
        import json as _json
        cleaned = sorted({str(s).strip().upper() for s in segments if s})
        if not cleaned:
            raise ValueError("at least one segment must remain enabled")
        self._store.set_source_state("instruments", "segments",
                                     _json.dumps(cleaned))
        return set(cleaned)

    def segment_counts(self) -> dict[str, int]:
        """Current catalog row count per MarketHub segment."""
        counts = self._store.segment_row_counts()
        return {k: v for k, v in sorted(counts.items())}

    def known_segments(self) -> list[str]:
        """Union of known segments and anything present in the catalog."""
        present = set(self.segment_counts())
        return sorted(set(_KNOWN_SEGMENTS) | present)

    def sync_fyers(self, *, fetch=None, segments: set[str] | None = None,
                   masters: list[str] | None = None) -> dict[str, Any]:
        """Refresh the FYERS catalogue master by master (segment-scoped).

        Each master file is an independent unit: a file that downloads and
        validates cleanly replaces ITS OWN segments; a file that fails
        keeps its previous rows. Nothing is deleted because another
        segment's download failed.

        ``masters`` restricts the sync to named master files (e.g.
        ``["BSE_FO"]``), so an operator can refresh the SENSEX leg without
        re-downloading - or touching - the NSE and BSE cash masters.

        The BSE_FO master is ingested under the Sensex selection policy:
        only authoritative-Sensex, active contracts are written, and the
        write is predicate-scoped, so the provider's other BSE_FO
        underlyings are preserved.
        """
        from app.catalog_sync import sync_fyers_masters

        fetch = fetch or _fetch
        enabled = segments if segments is not None \
            else self.get_enabled_segments()
        selected = self._select_fyers_masters(masters, enabled)
        result = sync_fyers_masters(
            self._store,
            fetch=fetch,
            urls=selected,
            parse=fyers_master_records,
            filter_records=_filter_sensex_bse_fo,
            enabled_segments=sorted(enabled),
            provider="fyers",
            validate_master=_validate_fyers_master_policy,
            report_fn=_sensex_master_report,
            write_scopes=FYERS_WRITE_SCOPES,
        )
        self._notify_sync(result)
        payload = result.to_dict()
        if "BSE_FO" in selected:
            try:
                payload["scope_audit"] = self.scope_audit("fyers")
            except Exception:
                logger.warning("fyers scope audit failed", exc_info=True)
        return payload

    @staticmethod
    def _select_fyers_masters(
        masters: list[str] | None, enabled: set[str],
    ) -> dict[str, str]:
        """The master files this sync will download (validated selection).

        An explicitly requested master whose segments are all disabled is
        a contradiction (the filter would discard its rows and the sync
        would report a misleading failure), so it is rejected up front.
        """
        if masters is None:
            return dict(FYERS_SEGMENT_URLS)
        wanted: list[str] = []
        for raw in masters:
            name = str(raw or "").strip().upper()
            if not name:
                continue
            if name not in FYERS_SEGMENT_URLS:
                raise ValueError(f"unknown fyers master: {raw!r}")
            if name in wanted:
                continue
            covered = FYERS_MASTER_SEGMENTS_MAP.get(name, ())
            if covered and not (set(covered) & enabled):
                raise ValueError(
                    f"fyers master {name} is disabled by the segment "
                    f"preference ({', '.join(covered)})")
            wanted.append(name)
        if not wanted:
            raise ValueError("masters must name at least one master file")
        return {name: FYERS_SEGMENT_URLS[name] for name in wanted}

    def scope_audit(self, provider: str, *, segment: str = "BSE_FO",
                    in_scope: frozenset[str] = SENSEX_UNDERLYINGS,
                    ) -> dict[str, Any]:
        """Read-only audit of what one provider's segment ALREADY holds.

        Reports the persisted catalogue grouped by underlying, split into
        the in-scope underlyings (Sensex) and everything else, so an
        operator can confirm that only in-scope rows were imported and
        that out-of-scope rows were preserved rather than deleted.
        """
        from core.persistence.modules.products import (
            open_instrument_connection,
            segment_underlying_summary,
        )

        conn = open_instrument_connection(self._store.db_path)
        try:
            rows = segment_underlying_summary(
                conn, provider=provider, segment=segment)
        finally:
            conn.close()

        def _totals(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
            return {
                "instruments": sum(int(r["instruments"]) for r in items),
                "futures": sum(int(r["futures"]) for r in items),
                "options": sum(int(r["options"]) for r in items),
                "calls": sum(int(r["calls"]) for r in items),
                "puts": sum(int(r["puts"]) for r in items),
                "expiries": sum(int(r["expiries"]) for r in items),
            }

        inside = [dict(r, in_scope=True) for r in rows
                  if r["underlying"] in in_scope]
        outside = [dict(r, in_scope=False) for r in rows
                   if r["underlying"] not in in_scope]
        return {
            "provider": provider,
            "segment": segment,
            "in_scope_underlyings": sorted(in_scope),
            "in_scope": _totals(inside),
            "out_of_scope": {
                **_totals(outside),
                "underlyings": sorted(r["underlying"] for r in outside),
            },
            "underlyings": inside + outside,
        }

    def sync_upstox(self, *, fetch=None, segments: set[str] | None = None,
                    ) -> dict[str, Any]:
        """Refresh the Upstox catalogue from the official BOD master.

        Fail-safe: a download/parse failure, or a master that is empty or
        structurally incomplete, ABORTS the sync and leaves the stored
        catalogue untouched (no delete, no partial write).

        BSE_FO ingestion is restricted to the supported SENSEX scope and
        replaced with a predicate-scoped write so rows outside that scope are
        preserved.
        """
        from app.catalog_sync import sync_upstox_master

        fetch = fetch or _fetch
        enabled = segments if segments is not None \
            else self.get_enabled_segments()
        result = sync_upstox_master(
            self._store,
            fetch=fetch,
            url=UPSTOX_MASTER_URL,
            parse=upstox_master_records,
            filter_records=_filter_sensex_bse_fo,
            enabled_segments=sorted(enabled),
            provider="upstox",
            validate_master=lambda master, records: _validate_bse_sensex_policy(
                master, records, enabled_segments=set(enabled)),
            report_fn=_sensex_master_report,
            write_scopes=UPSTOX_WRITE_SCOPES if "BSE_FO" in enabled else {},
        )
        self._notify_sync(result)
        payload = result.to_dict()
        if "BSE_FO" in enabled:
            try:
                payload["scope_audit"] = self.scope_audit("upstox")
            except Exception:
                logger.warning("upstox scope audit failed", exc_info=True)
        return payload

    def search(self, **kw: Any) -> list[dict[str, Any]]:
        return self._store.search_instruments(**kw)

    def browse(self, **kw: Any) -> dict[str, Any]:
        """Paginated, provider-deduped browse over the canonical catalog."""
        return self._store.browse_instruments(**kw)

    def pool(self, **kw: Any) -> list[dict[str, Any]]:
        """Curated navigation pool for the native symbol picker."""
        return self._store.instrument_pool(**kw)

    def find_exact_rows(self, symbol: str, *, exchange: str | None = None,
                        instrument_type: str | None = None,
                        ) -> list[dict[str, Any]]:
        """Rows whose canonical symbol/underlying matches EXACTLY.

        The substring search can match an unrelated instrument or fall
        outside its LIMIT window; exact resolution never guesses.
        """
        return list(self._store.find_by_exact_symbol(
            symbol, instrument_type=instrument_type,
            exchange=exchange) or [])

    def derivative_expiries(self, underlying: str, instrument_type: str,
                            exchange: str | None = None) -> list[str]:
        return self._store.derivative_expiries(underlying, instrument_type,
                                               exchange=exchange)

    def catalog_epoch(self) -> int:
        """Monotonic catalog version (changes on every catalog replace).

        The catalogue's own revision is authoritative for the
        segment-scoped synchronisations (they do not pass through the
        event store's provider-wide replace); the store's epoch is
        included so a replace performed elsewhere is never missed.
        """
        try:
            store_epoch = int(self._store.catalog_epoch())
        except Exception:
            store_epoch = 0
        return max(self._revision, store_epoch)

    def fno_universe(self, *, provider: str | None = None, today: str,
                     q: str | None = None, limit: int = 500) -> list[dict]:
        """Equity underlyings with non-expired F&O contracts (read model)."""
        return self._store.fno_universe(provider=provider, today=today,
                                        q=q, limit=limit)

    def fno_underlying_rows(self, *, provider: str | None = None, today: str,
                            limit: int = 2000) -> list[dict]:
        """Enumeration read model: F&O stock underlyings (no counts)."""
        return self._store.fno_underlying_rows(provider=provider, today=today,
                                               limit=limit)

    def list_future_contracts(self, **kw: Any) -> list[dict[str, Any]]:
        """Bulk future-contract read model (one query, provider-neutral)."""
        return self._store.list_future_contracts(**kw)

    def option_strikes(self, underlying: str, expiry: str,
                       exchange: str | None = None) -> list[dict]:
        return self._store.option_strikes(underlying, expiry,
                                          exchange=exchange)

    def get(self, provider: str, token: str) -> dict[str, Any] | None:
        return self._store.get_instrument(provider, token)

    def find_equity(self, symbol: str, *, provider: str | None = None,
                    exchange: str | None = "NSE") -> dict[str, Any] | None:
        """Find the EXACT equity cash row for a canonical symbol.

        The catalogue search is a substring LIKE with a LIMIT, so a short
        symbol (e.g. "LT") would otherwise match an unrelated equity
        ("20MICRONS", "3BBLACKBIO", ...) or fall outside the limit
        window entirely. This uses the EXACT-symbol read model.
        """
        want = (symbol or "").strip().upper()
        if not want:
            return None
        try:
            rows = self._store.find_by_exact_symbol(
                want, provider=provider, instrument_type="EQUITY",
                exchange=exchange)
        except Exception:
            rows = []
        for row in rows or []:
            if str(row.get("tradingsymbol") or "").upper() == want:
                return row
            if str(row.get("underlying") or "").upper() == want:
                return row
        return rows[0] if rows else None

    def quote_key(self, row: dict[str, Any]) -> str | None:
        """The key under which this row's QUOTE STATE is stored.

        The catalogue owns the provider-native identifier ROLES, so the
        quote key is asked of the catalogue - it is never derived from an
        identifier's string shape.
        """
        from app.canonical_identity import provider_quote_key
        return provider_quote_key(row)

    def find_index(self, symbol: str, *, provider: str | None = None,
                   exchange: str | None = None) -> dict[str, Any] | None:
        """Find the EXACT index row for a canonical symbol/underlying."""
        rows = self.find_index_rows(symbol, provider=provider,
                                    exchange=exchange)
        return rows[0] if rows else None

    def find_index_rows(self, symbol: str, *, provider: str | None = None,
                        exchange: str | None = None) -> list[dict[str, Any]]:
        """Every EXACT index row for a canonical symbol/underlying.

        One row per PROVIDER — the index is listed by both, and each has
        its own identifier for it.
        """
        want = (symbol or "").strip().upper()
        if not want:
            return []
        try:
            rows = self._store.find_by_exact_symbol(
                want, provider=provider, instrument_type="INDEX",
                exchange=exchange)
        except Exception:
            rows = []
        out: list[dict[str, Any]] = []
        for row in rows or []:
            if str(row.get("underlying") or "").upper() == want:
                out.append(row)
                continue
            tsym = str(row.get("tradingsymbol") or "").upper()
            if tsym == want or tsym.endswith(f":{want}"):
                out.append(row)
        return out

    def find_canonical_rows(self, canonical_id: str,
                            ) -> list[dict[str, Any]]:
        """Every catalogue row that carries this canonical id.

        The catalogue stores provider-native rows; the canonical id is
        DERIVED from them (``app.canonical_identity.canonical_id_for_row``),
        so a row is located by the provider-independent parts the id is
        made of:

          * ``EXCHANGE:EQUITY:ISIN`` — the ISIN (the provider-independent
            identity of a cash instrument), or the cash symbol for a row
            without an ISIN;
          * ``EXCHANGE:INDEX:SYMBOL`` — the index symbol;
          * ``EXCHANGE:FUTURE:U:EXPIRY`` — underlying + expiry;
          * ``EXCHANGE:OPTION:U:EXPIRY:STRIKE:TYPE`` — underlying +
            expiry + strike + option type.

        The parts are matched EXACTLY, so the result is the complete set of
        providers that list the contract — the set the subscription
        manager routes over. A row is kept only when its DERIVED canonical
        id equals the requested one, so a near-miss can never be routed
        as if it were the same contract.
        """
        from app.canonical_identity import canonical_id_for_row
        want = (canonical_id or "").strip()
        if not want:
            return []
        parts = want.split(":")
        if len(parts) < 3:
            return []
        exchange, kind = parts[0], parts[1]
        rest = parts[2:]
        rows: list[dict[str, Any]] = []
        try:
            if kind in ("EQUITY", "ETF"):
                symbol = rest[0]
                # The ISIN is the cross-provider identity of a cash
                # instrument; fall back to the canonical symbol for a row
                # that carries no ISIN.
                rows = list(self._store.search_instruments(
                    isin=symbol, exchange=exchange,
                    instrument_type=kind, limit=20) or [])
                if not rows:
                    rows = list(self.find_equity(
                        symbol, exchange=exchange) or [])
            elif kind == "INDEX":
                rows = list(self.find_index_rows(
                    rest[0], exchange=exchange) or [])
            elif kind == "FUTURE" and len(rest) >= 2:
                rows = list(self._store.search_instruments(
                    underlying=rest[0], expiry=rest[1], exchange=exchange,
                    instrument_type="FUTURE", limit=20) or [])
            elif kind == "OPTION" and len(rest) >= 4:
                try:
                    strike = float(rest[2])
                except (TypeError, ValueError):
                    return []
                rows = list(self._store.search_instruments(
                    underlying=rest[0], expiry=rest[1], strike=strike,
                    option_type=rest[3], exchange=exchange,
                    instrument_type="OPTION", limit=20) or [])
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            try:
                if canonical_id_for_row(row) == want:
                    out.append(row)
            except Exception:
                continue
        return out

    def get_by_key(self, instrument_key: str) -> dict[str, Any] | None:
        """Find the catalogue row that OWNS a provider-native key.

        A key is owned by the provider that published it: the Upstox
        instrument key, the Fyers API symbol, or the Fyers fyToken. The
        catalogue is the authority, so a caller never has to infer the
        provider from the key's string shape.
        """
        if not instrument_key:
            return None
        key = str(instrument_key)
        for provider in ("upstox", "fyers"):
            try:
                row = self.get(provider, key)
            except Exception:
                row = None
            if row:
                return row
        # The Fyers REST/feed key is the API SYMBOL, which the catalogue
        # stores in ``provider_symbol`` rather than ``instrument_token``.
        try:
            row = self._store.get_instrument_by_provider_symbol(key)
        except Exception:
            row = None
        if row:
            return row
        try:
            rows = self.search(q=key, limit=10)
        except Exception:
            rows = []
        for row in rows or []:
            if str(row.get("provider_symbol") or "") == key:
                return row
        return None

    # -- provider-native identifiers (Phase A) ----------------------------

    def provider_identifiers(self, row: dict[str, Any]) -> dict[str, Any]:
        """Provider-native identifiers for one catalogue row, BY ROLE.

        See :func:`app.canonical_identity.provider_identifiers_for_row`:
        ``feed_key`` (stream + quote storage), ``rest_key`` (REST request),
        ``token`` (the provider's own token — NEVER a request identifier on
        its own).
        """
        from app.canonical_identity import provider_identifiers_for_row
        return provider_identifiers_for_row(row)

    def canonical_id(self, row: dict[str, Any]) -> str | None:
        """Provider-neutral canonical id for one catalogue row."""
        from app.canonical_identity import canonical_id_for_row
        return canonical_id_for_row(row)

    def resolve_provider_reference(
        self, *, provider: str, underlying: str, instrument_type: str,
        expiry: str | None = None, exchange: str | None = None,
    ) -> dict[str, Any] | None:
        """Resolve one UNDERLYING to a provider's own identifiers.

        Returns the catalogue row for the provider's own representation of
        the underlying, plus its role-tagged provider identifiers.

        This is the ONE place a provider request identifier is resolved for
        a whole underlying (e.g. the Fyers option-chain request symbol).
        It never guesses from an identifier's string shape: the provider's
        own catalogue row is the source of truth.
        """
        symbol = (underlying or "").strip().upper()
        if not symbol:
            return None
        rows = self.search(underlying=symbol, instrument_type=instrument_type,
                           provider=provider, limit=50)
        if not rows:
            rows = self.search(q=symbol, instrument_type=instrument_type,
                               provider=provider, limit=50)
        exact = None
        for row in rows or []:
            row_sym = str(row.get("underlying") or "").upper()
            if row_sym == symbol:
                exact = row
                break
            row_tsym = str(row.get("tradingsymbol") or "").upper()
            if row_tsym == symbol or row_tsym.endswith(f":{symbol}"):
                exact = row
                break
        if exact is None and rows:
            exact = rows[0]
        if exact is None:
            return None
        ids = self.provider_identifiers(exact)
        ids["row"] = exact
        ids["canonical_id"] = self.canonical_id(exact)
        return ids

    def equity_universe(self, *, provider: str | None = None,
                       limit: int = 5000) -> list[dict[str, Any]]:
        """All NSE equity instruments (read model for breadth/heatmap)."""
        return self._store.equity_universe(provider=provider, limit=limit)

    def sync_state(self) -> list[dict[str, Any]]:
        return self._store.instruments_sync_state()
