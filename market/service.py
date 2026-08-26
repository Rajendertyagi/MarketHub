"""
MarketService — shared in-process market state for MarketHub (Phase B2).

Constructed ONCE by application composition (Phase C wiring); standalone and
unwired in B2. Owns ONLY state semantics:

  * latest canonical Quote per instrument  (merged from QuotePatch updates)
  * latest canonical Depth per instrument  (whole-snapshot replacement)
  * deterministic stale/out-of-order handling
  * immutable snapshot reads
  * minimal service-local counters
  * one optional post-merge quote callback (failure-isolated)

Does NOT own: provider parsing, network, WebSocket/REST, Starlette, MCP,
persistence, SSE broadcasting. No global singleton — dependency-injected.

Canonical key
-------------
State is keyed by ``(exchange, instrument_token)``.

``tradingsymbol`` is deliberately NOT part of the key: symbols carry
exchange/segment suffix conventions that vary by provider and can be
ambiguous, while (exchange, token) is the stable routing identity.
Cross-provider ISIN unification is explicitly out of scope for B2.

QuotePatch presence contract (mirrors the B1 field-map contract)
----------------------------------------------------------------
    reported_fields key ABSENT      -> preserve previous canonical value
    reported_fields key PRESENT     -> set canonical value (None clears it)

A normal Optional-fields dataclass would lose this distinction, so patches
carry an explicit mapping instead. The frozen canonical ``Quote`` is never
used as a patch DTO.

Staleness / ordering algorithm (deterministic, documented)
----------------------------------------------------------
Ordering timestamp per side:
    exchange_ts when that side has one, else received_ts.

Comparison domain:
    * both sides have exchange_ts  -> compare in the exchange_ts domain
    * otherwise                    -> compare received_ts on both sides
      (received_ts always exists: required on Quote and QuotePatch alike;
      this avoids comparing unrelated clock domains asymmetrically)

    incoming >  current -> accept
    incoming <  current -> reject as stale (MergeOutcome, never an exception)
    incoming == current -> accept, last arrival wins

There is NO REST-vs-WS source priority anywhere: timestamps decide.

Callback
--------
``on_quote_update`` fires AFTER state commit, only for accepted+changed
quotes, receives the resulting immutable Quote, supports sync or async
callables, and its failures are logged and swallowed — state is never
rolled back and apply_quote never fails because of a callback.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping

from market.models import Depth, Quote, merge_greeks
from market.normalize.common import QUOTE_FIELD_NAMES

logger = logging.getLogger(__name__)

__all__ = [
    "MarketServiceError",
    "QuotePatch",
    "MergeOutcome",
    "MarketService",
]

# Canonical STATE fields a patch may report. Identity fields
# (instrument_token/exchange/tradingsymbol) and timestamps (received_ts/
# exchange_ts) are structured attributes of QuotePatch, not arbitrary patch
# fields — they must never appear inside reported_fields.
PATCH_FIELD_NAMES = frozenset(
    QUOTE_FIELD_NAMES
    - {"instrument_token", "exchange", "tradingsymbol", "received_ts", "exchange_ts"}
)


class MarketServiceError(ValueError):
    """Invalid input to the market service (malformed patch or identity)."""


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime):
        raise MarketServiceError(f"QuotePatch.{name} must be a datetime")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise MarketServiceError(f"QuotePatch.{name} must be timezone-aware")


@dataclass(frozen=True)
class QuotePatch:
    """Provider-neutral normalized partial quote update (presence-explicit).

    Attributes:
        exchange / instrument_token: canonical identity (the state key).
        received_ts: REQUIRED UTC-aware MarketHub acceptance stamp.
        reported_fields: canonical state field -> parsed value; key present
            means "reported" (None = provider explicitly sent null), key
            absent means "not reported". Unknown keys are rejected here,
            deterministically at construction.
        tradingsymbol: metadata; REQUIRED on the first patch for an
            instrument (Quote construction needs it), optional afterwards.
        exchange_ts: optional UTC-aware provider timestamp used for ordering.
    """

    exchange: str
    instrument_token: str
    received_ts: datetime
    reported_fields: Mapping[str, Any] = field(default_factory=dict)
    tradingsymbol: str | None = None
    exchange_ts: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.exchange, str) or not self.exchange.strip():
            raise MarketServiceError("QuotePatch.exchange must be a non-empty string")
        if (
            not isinstance(self.instrument_token, str)
            or not self.instrument_token.strip()
        ):
            raise MarketServiceError(
                "QuotePatch.instrument_token must be a non-empty string"
            )
        _require_aware(self.received_ts, "received_ts")
        if self.exchange_ts is not None:
            _require_aware(self.exchange_ts, "exchange_ts")
        if self.tradingsymbol is not None and (
            not isinstance(self.tradingsymbol, str) or not self.tradingsymbol.strip()
        ):
            raise MarketServiceError(
                "QuotePatch.tradingsymbol must be a non-empty string or None"
            )
        if not isinstance(self.reported_fields, Mapping):
            raise MarketServiceError("QuotePatch.reported_fields must be a mapping")
        unknown = set(self.reported_fields) - PATCH_FIELD_NAMES
        if unknown:
            raise MarketServiceError(
                f"unknown patch fields (provider aliases must die in "
                f"normalizers): {sorted(unknown)}"
            )
        # Snapshot into a read-only mapping: isolated from the caller's
        # original object AND genuinely immutable afterwards — item
        # assignment, addition and deletion all raise TypeError.
        object.__setattr__(
            self,
            "reported_fields",
            MappingProxyType(dict(self.reported_fields)),
        )

    @property
    def key(self) -> tuple[str, str]:
        """Canonical state key for this patch."""
        return (self.exchange, self.instrument_token)


@dataclass(frozen=True)
class MergeOutcome:
    """Explicit result of an apply_* call. Stale data NEVER raises."""

    accepted: bool
    created: bool
    stale: bool
    changed: bool
    key: tuple[str, str]
    reason: str | None = None


_QuoteCallback = Callable[[Quote], Any]  # sync return or awaitable


class MarketService:
    """In-process market state owner. See module docstring for semantics."""

    def __init__(self, *, on_quote_update: _QuoteCallback | None = None) -> None:
        self._on_quote_update = on_quote_update
        self._lock = asyncio.Lock()
        self._quotes: dict[tuple[str, str], Quote] = {}
        self._depths: dict[tuple[str, str], Depth] = {}
        self._counters: dict[str, int] = {
            "quote_count": 0,
            "depth_count": 0,
            "accepted_quote_updates": 0,
            "stale_quote_updates": 0,
            "accepted_depth_updates": 0,
            "stale_depth_updates": 0,
        }

    def get_quote_now(self, exchange: str,
                      instrument_token: str) -> Quote | None:
        """Synchronous lock-free quote read (for non-async consumers)."""
        return self._quotes.get((exchange, instrument_token))

    def get_depth_now(self, exchange: str,
                      instrument_token: str) -> Depth | None:
        """Synchronous lock-free depth read (for non-async consumers)."""
        return self._depths.get((exchange, instrument_token))

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _orders(
        inc_ex: datetime | None,
        inc_recv: datetime,
        cur_ex: datetime | None,
        cur_recv: datetime,
    ) -> tuple[datetime, datetime]:
        """Select ONE comparison domain (locked staleness algorithm).

        When both sides carry exchange_ts, compare in the exchange domain;
        otherwise BOTH sides fall back to received_ts, so unrelated clock
        domains are never compared asymmetrically.
        """
        if inc_ex is not None and cur_ex is not None:
            return inc_ex, cur_ex
        return inc_recv, cur_recv

    def _merge_quote(self, current: Quote, patch: QuotePatch) -> Quote:
        """Immutable merge per the locked presence rules (1-3).

        Greeks exception: a reported greeks snapshot merges FIELD-WISE
        over prior state (merge_greeks) so a partial provider snapshot
        cannot discard previously reported values. Whole-object clear is
        expressed as greeks=None... which per rule 2 would clear — but a
        None INSIDE the snapshot means "not reported" and preserves.
        """
        values = {name: getattr(current, name) for name in PATCH_FIELD_NAMES}
        for name, value in patch.reported_fields.items():
            if name == "greeks" and value is not None:
                values[name] = merge_greeks(current.greeks, value)
            else:
                values[name] = value  # None clears; a value replaces
        return Quote(
            instrument_token=current.instrument_token,
            exchange=current.exchange,
            tradingsymbol=(
                patch.tradingsymbol
                if patch.tradingsymbol is not None
                else current.tradingsymbol
            ),
            received_ts=patch.received_ts,
            exchange_ts=(
                patch.exchange_ts if patch.exchange_ts is not None else current.exchange_ts
            ),
            **values,
        )

    def _build_initial_quote(self, patch: QuotePatch) -> Quote:
        values = {name: patch.reported_fields.get(name) for name in PATCH_FIELD_NAMES}
        return Quote(
            instrument_token=patch.instrument_token,
            exchange=patch.exchange,
            tradingsymbol=str(patch.tradingsymbol),
            received_ts=patch.received_ts,
            exchange_ts=patch.exchange_ts,
            **values,
        )

    async def _notify(self, quote: Quote) -> None:
        """Fire the post-merge callback with deterministic failure isolation."""
        callback = self._on_quote_update
        if callback is None:
            return
        try:
            result = callback(quote)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception(
                "market service: on_quote_update callback failed "
                "(state already committed; failure isolated)"
            )

    # -- quote API ----------------------------------------------------------

    async def apply_quote(self, patch: QuotePatch) -> MergeOutcome:
        """Apply a normalized partial quote update.

        Critical section: read current -> stale check -> merge -> commit.
        The callback runs OUTSIDE the lock after a successful commit.
        """
        key = patch.key
        async with self._lock:
            current = self._quotes.get(key)
            if current is not None:
                incoming_order, current_order = self._orders(
                    patch.exchange_ts, patch.received_ts,
                    current.exchange_ts, current.received_ts,
                )
                if incoming_order < current_order:
                    self._counters["stale_quote_updates"] += 1
                    return MergeOutcome(
                        accepted=False, created=False, stale=True, changed=False,
                        key=key, reason="stale",
                    )
                merged = self._merge_quote(current, patch)
                created = False
            else:
                if patch.tradingsymbol is None:
                    raise MarketServiceError(
                        f"first patch for {key} requires tradingsymbol"
                    )
                merged = self._build_initial_quote(patch)
                created = True

            changed = merged != current  # created implies changed
            self._quotes[key] = merged
            self._counters["accepted_quote_updates"] += 1
            if created:
                self._counters["quote_count"] += 1

        if changed:
            await self._notify(merged)
        return MergeOutcome(
            accepted=True, created=created, stale=False, changed=changed, key=key
        )

    async def get_quote(self, exchange: str, instrument_token: str) -> Quote | None:
        """Return the stored immutable Quote, or None. Lock-free safe read."""
        return self._quotes.get((exchange, instrument_token))

    async def quotes(self) -> tuple[Quote, ...]:
        """Snapshot of all stored quotes (immutable objects)."""
        return tuple(self._quotes.values())

    # -- depth API ----------------------------------------------------------

    async def apply_depth(self, depth: Depth) -> MergeOutcome:
        """Replace the full Depth snapshot for an instrument.

        Same ordering/staleness policy as quotes; no bid/ask merging between
        snapshots (Depth is a complete normalized object).
        """
        key = (depth.exchange, depth.instrument_token)
        async with self._lock:
            current = self._depths.get(key)
            if current is not None:
                incoming_order, current_order = self._orders(
                    depth.exchange_ts, depth.received_ts,
                    current.exchange_ts, current.received_ts,
                )
                if incoming_order < current_order:
                    self._counters["stale_depth_updates"] += 1
                    return MergeOutcome(
                        accepted=False, created=False, stale=True, changed=False,
                        key=key, reason="stale",
                    )
                created = False
            else:
                created = True

            changed = depth != current  # created implies changed
            self._depths[key] = depth
            self._counters["accepted_depth_updates"] += 1
            if created:
                self._counters["depth_count"] += 1

        return MergeOutcome(
            accepted=True, created=created, stale=False, changed=changed, key=key
        )

    async def get_depth(self, exchange: str, instrument_token: str) -> Depth | None:
        """Return the stored immutable Depth, or None. Lock-free safe read."""
        return self._depths.get((exchange, instrument_token))

    async def depths(self) -> tuple[Depth, ...]:
        """Snapshot of all stored depths (immutable objects)."""
        return tuple(self._depths.values())

    # -- diagnostics --------------------------------------------------------

    async def status(self) -> dict[str, int]:
        """Minimal service-local diagnostic counters (not a metrics framework)."""
        return dict(self._counters)
