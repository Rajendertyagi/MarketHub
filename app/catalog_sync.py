"""Fail-safe, segment-scoped instrument catalogue synchronisation.

The catalogue is a **derived cache** of the providers' master files. A
sync must therefore never destroy valid stored data because a download,
a parse or a partial master went wrong.

Rules enforced here:

* a download or parse failure aborts the affected unit of work and writes
  nothing;
* an EMPTY or structurally incomplete master is rejected before any write
  (completeness invariants, not an arbitrary shrinkage percentage);
* for multi-file providers (FYERS) each master file is a unit: a file
  that fails keeps its PREVIOUS rows, while files that downloaded and
  validated cleanly are replaced;
* the write itself is transactional and segment-scoped, so only the
  segments that were actually refreshed are touched.

The validation invariants are documented next to each check.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("event_server")

#: Provider master files (FYERS publishes one master PER segment).
#: The official master set is NSE_CM, NSE_FO, NSE_CD, NSE_COM, BSE_CM,
#: BSE_FO, MCX_COM. MarketHub's scope is the NSE F&O universe plus the
#: BSE (SENSEX) leg, so the covered masters are exactly the ones that
#: can contribute in-scope contracts: MCX commodities are out of scope.
FYERS_MASTER_SEGMENTS: tuple[str, ...] = ("NSE_CM", "NSE_FO", "BSE_CM",
                                           "BSE_FO")

#: Which canonical segments each FYERS master can contribute.
#: FYERS publishes the NSE/BSE INDEX rows inside the CASH masters.
FYERS_MASTER_SEGMENTS_MAP: dict[str, tuple[str, ...]] = {
    "NSE_CM": ("NSE_EQ", "NSE_INDEX"),
    "NSE_FO": ("NSE_FO",),
    "BSE_CM": ("BSE_EQ", "BSE_INDEX"),
    "BSE_FO": ("BSE_FO",),
}

#: Structural invariant of the Upstox BOD master (a single file that
#: carries every exchange and instrument type). A valid file always
#: contains at least one row of each instrument family it publishes.
UPSTOX_MASTER_INSTRUMENT_TYPES: tuple[str, ...] = ("EQUITY", "FUTURE",
                                                    "OPTION", "INDEX")

#: Segments the official Upstox BOD master publishes. A master that lost
#: one of them is a partial download. An ENABLED segment the provider
#: does not publish is simply not required (never an abort reason).
UPSTOX_MASTER_SEGMENTS: frozenset[str] = frozenset({
    "NSE_EQ", "NSE_FO", "NSE_INDEX", "NSE_COM",
    "BSE_EQ", "BSE_FO", "BSE_INDEX",
    "MCX_FO", "BCD_FO", "NCD_FO",
})

#: Instrument families each canonical segment contains. A segment that is
#: present but carries rows of another family is a mis-parsed master.
SEGMENT_FAMILIES: dict[str, tuple[str, ...]] = {
    "NSE_EQ": ("EQUITY", "ETF"), "BSE_EQ": ("EQUITY", "ETF"),
    # NSE_COM is the NSE commodity segment (futures/options).
    "NSE_COM": ("FUTURE", "OPTION"),
    "NSE_FO": ("FUTURE", "OPTION"), "BSE_FO": ("FUTURE", "OPTION"),
    "MCX_FO": ("FUTURE", "OPTION"), "BCD_FO": ("FUTURE", "OPTION"),
    "NCD_FO": ("FUTURE", "OPTION"),
    "NSE_INDEX": ("INDEX",), "BSE_INDEX": ("INDEX",),
}


@dataclass(frozen=True)
class WriteScope:
    """Predicate scope for a selectively ingested master.

    ``column`` must be a real catalog column; ``values`` must be non-empty.
    Rows of the same provider/segment outside the scope are preserved.
    """

    segment: str
    column: str
    values: tuple[str, ...]


@dataclass
class SyncResult:
    """Outcome of one catalogue synchronisation."""

    provider: str
    ok: bool = True
    aborted: bool = False
    reason: str = ""
    records: int = 0
    parsed: int = 0
    kept: int = 0
    filtered: int = 0
    enabled: list[str] = field(default_factory=list)
    #: canonical segment -> number of kept rows (the legacy contract)
    segment_counts: dict[str, int] = field(default_factory=dict)
    #: master -> "updated" | "retained" | "failed"
    masters: dict[str, str] = field(default_factory=dict)
    #: human readable failures (per master for multi-file providers)
    failures: list[str] = field(default_factory=list)
    #: master -> policy-specific counters (selection evidence)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": "ok" if self.ok else "failed",
            "ok": self.ok,
            "aborted": self.aborted,
            "reason": self.reason,
            "records": self.records,
            "parsed": self.parsed,
            "kept": self.kept,
            "filtered": self.filtered,
            "enabled": sorted(self.enabled),
            "segments": dict(self.segment_counts),
            "masters": dict(self.masters),
            "failures": list(self.failures),
            "details": dict(self.details),
        }


class MasterValidationError(RuntimeError):
    """A master file is empty or structurally incomplete."""


def _segment_of(record: dict[str, Any]) -> str | None:
    seg = record.get("segment")
    return str(seg) if seg else None


def validate_upstox_master(records: Sequence[dict[str, Any]],
                           enabled_segments: Iterable[str]) -> None:
    """Reject an Upstox master that is empty or structurally incomplete.

    Invariants (from the official BOD master, which carries every
    exchange and instrument family):

    * at least one record was parsed;
    * every ENABLED canonical segment **that the provider publishes** is
      represented - a master that silently lost a whole segment is a
      partial download, not a smaller market;
    * every instrument family the master publishes is represented.

    Raises :class:`MasterValidationError` (nothing is written).
    """
    if not records:
        raise MasterValidationError("master parsed to zero records")
    required = [s for s in enabled_segments
                if s in UPSTOX_MASTER_SEGMENTS]
    by_segment: dict[str, set[str]] = {}
    for rec in records:
        seg = _segment_of(rec)
        if seg:
            by_segment.setdefault(seg, set()).add(
                str(rec.get("instrument_type") or ""))
    missing = [s for s in required if s not in by_segment]
    if missing:
        raise MasterValidationError(
            "master is missing enabled segment(s): " + ",".join(sorted(missing)))
    # A segment that is present must actually carry its own family (a
    # segment full of rows of another family means a mis-parsed master).
    wrong_family = []
    for seg in required:
        families = SEGMENT_FAMILIES.get(seg)
        if not families:
            continue
        if not (by_segment.get(seg, set()) & set(families)):
            wrong_family.append(seg)
    if wrong_family:
        raise MasterValidationError(
            "master segment(s) carry unexpected instrument families: "
            + ",".join(sorted(wrong_family)))


def validate_fyers_master(master: str,
                           records: Sequence[dict[str, Any]]) -> None:
    """Reject an empty FYERS master file.

    Each FYERS master is a dict keyed by ``EXCH:SYMBOL``; a valid one
    always yields records. Zero records means a truncated / replaced /
    empty file and must not replace stored rows.
    """
    if not records:
        raise MasterValidationError(
            f"{master} master parsed to zero records")


def sync_upstox_master(
    store: Any,
    *,
    fetch: Callable[[str], bytes],
    url: str,
    parse: Callable[[bytes], list[dict[str, Any]]],
    filter_records: Callable[[list[dict[str, Any]], Iterable[str]],
                              tuple[list[dict[str, Any]], dict[str, int], int]],
    enabled_segments: Sequence[str],
    provider: str = "upstox",
) -> SyncResult:
    """Refresh the Upstox catalogue from one master file.

    The whole provider is one unit here (Upstox publishes a single BOD
    master), so a failure leaves the stored catalogue untouched.
    """
    result = SyncResult(provider=provider, enabled=list(enabled_segments))
    try:
        raw = fetch(url)
    except Exception as exc:
        result.ok = False
        result.aborted = True
        result.reason = f"download failed: {type(exc).__name__}"
        result.failures.append(result.reason)
        logger.warning("upstox catalogue sync aborted (%s)", result.reason)
        return result
    try:
        parsed_records = parse(raw)
    except Exception as exc:
        result.ok = False
        result.aborted = True
        result.reason = f"parse failed: {type(exc).__name__}"
        result.failures.append(result.reason)
        logger.warning("upstox catalogue sync aborted (%s)", result.reason)
        return result

    result.parsed = len(parsed_records)
    try:
        validate_upstox_master(parsed_records, enabled_segments)
    except MasterValidationError as exc:
        result.ok = False
        result.aborted = True
        result.reason = str(exc)
        result.failures.append(result.reason)
        logger.warning("upstox catalogue sync aborted (%s)", result.reason)
        return result

    kept, seg_counts, filtered = filter_records(parsed_records,
                                                enabled_segments)
    if not kept:
        result.ok = False
        result.aborted = True
        result.reason = "no records survived the segment filter"
        result.failures.append(result.reason)
        return result
    result.kept = len(kept)
    result.filtered = filtered
    result.records = store.replace_provider_instruments(provider, kept)
    result.segment_counts = dict(seg_counts)
    result.masters = {"complete": "updated"}
    logger.info("upstox instrument sync: %d kept / %d parsed (%d filtered)",
                result.records, result.parsed, filtered)
    return result


def sync_fyers_masters(
    store: Any,
    *,
    fetch: Callable[[str], bytes],
    urls: dict[str, str],
    parse: Callable[[bytes], list[dict[str, Any]]],
    filter_records: Callable[[list[dict[str, Any]], Iterable[str]],
                              tuple[list[dict[str, Any]], dict[str, int], int]],
    enabled_segments: Sequence[str],
    provider: str = "fyers",
    validate_master: Callable[[str, Sequence[dict[str, Any]]], None] | None = None,
    report_fn: Callable[[str, Sequence[dict[str, Any]]],
                        dict[str, Any] | None] | None = None,
    write_scopes: dict[str, WriteScope] | None = None,
) -> SyncResult:
    """Refresh the FYERS catalogue master by master (segment-scoped).

    Each master file is an independent unit:

    * a master that downloads and validates cleanly REPLACES its own
      segments;
    * a master that fails keeps its PREVIOUS rows untouched;
    * if every master fails nothing is written at all.

    A provider row is only replaced for the segments the successful
    masters actually cover, so a failed master can never delete data.

    ``validate_master`` adds policy invariants (e.g. the Sensex selection
    shape) before any write. ``report_fn`` records selection evidence per
    master. ``write_scopes`` switches a master from a segment-wide replace
    to a predicate-scoped replace, preserving other underlyings in the
    same segment.
    """
    result = SyncResult(provider=provider, enabled=list(enabled_segments))
    enabled = set(enabled_segments)
    covered_segments: set[str] = set()
    written = 0
    scopes = write_scopes or {}

    for master, url in urls.items():
        try:
            raw = fetch(url)
        except Exception as exc:
            reason = f"{master}: download failed: {type(exc).__name__}"
            result.failures.append(reason)
            result.masters[master] = "failed"
            logger.warning("fyers catalogue segment skipped (%s)", reason)
            continue
        try:
            parsed_records = parse(raw)
        except Exception as exc:
            # The parse error carries the affected contract and the failed
            # field for the policy checks, so it is part of the evidence.
            reason = (f"{master}: parse failed: {type(exc).__name__}: "
                      f"{exc}")
            result.failures.append(reason)
            result.masters[master] = "failed"
            logger.warning("fyers catalogue segment skipped (%s)", reason)
            continue
        try:
            validate_fyers_master(master, parsed_records)
            if validate_master is not None:
                validate_master(master, parsed_records)
        except MasterValidationError as exc:
            reason = f"{master}: {exc}"
            result.failures.append(reason)
            result.masters[master] = "failed"
            logger.warning("fyers catalogue segment skipped (%s)", reason)
            continue

        result.parsed += len(parsed_records)
        if report_fn is not None:
            report = report_fn(master, parsed_records)
            if report is not None:
                result.details[master] = dict(report)
        kept, _seg_counts, filtered = filter_records(parsed_records, enabled)
        result.filtered += filtered
        if not kept:
            reason = f"{master}: no records survived the segment filter"
            result.failures.append(reason)
            result.masters[master] = "failed"
            # The failure must be VISIBLE: a master that parses but
            # contributes nothing is exactly the silent coverage gap this
            # path exists to prevent.
            logger.warning("fyers catalogue segment skipped (%s)", reason)
            continue
        result.kept += len(kept)
        scope = scopes.get(master)
        segments = [scope.segment] if scope else sorted(
            {s for s in (_segment_of(r) for r in kept) if s})
        try:
            # The write lives in the persistence module that owns the
            # instruments table (core.persistence.modules.products) and is
            # addressed by the store's PUBLIC db_path - the protected
            # event-store module is not modified.
            from core.persistence.modules import products as _products
            if scope is not None:
                count = _products.replace_provider_instruments_scoped_at(
                    store.db_path, provider, kept,
                    segment=scope.segment, scope_column=scope.column,
                    scope_values=scope.values)
            else:
                count = _products.replace_provider_instruments_segmented_at(
                    store.db_path, provider, kept, segments)
        except Exception as exc:
            reason = f"{master}: write failed: {type(exc).__name__}"
            result.failures.append(reason)
            result.masters[master] = "failed"
            if master in result.details:
                result.details[master]["persisted"] = 0
            logger.warning("fyers catalogue segment write failed (%s)", reason)
            continue
        written += count
        covered_segments.update(segments)
        result.masters[master] = "updated"
        if master in result.details:
            result.details[master]["persisted"] = count
        for seg in segments:
            result.segment_counts[seg] = (
                result.segment_counts.get(seg, 0)
                + len([r for r in kept if _segment_of(r) == seg]))

    if not any(status == "updated" for status in result.masters.values()):
        result.ok = False
        result.aborted = True
        result.reason = "no master could be refreshed; catalogue unchanged"
        return result

    # A master that failed keeps its previous rows - report them as
    # retained so the operator can see the gap.
    for master, status in list(result.masters.items()):
        if status == "failed":
            result.masters[master] = "retained"
    result.records = written
    logger.info("fyers instrument sync: %d written across %d master(s); "
                "segments covered=%s", written,
                sum(1 for s in result.masters.values() if s == "updated"),
                ",".join(sorted(covered_segments)))
    return result
