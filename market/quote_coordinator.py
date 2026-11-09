"""Central quote coordinator — READ-side selection over the existing store.

The native-keyed store (``MarketService._quotes``) holds one slot per
provider per instrument. This module adds a deterministic, freshness-gated
selection layer that picks exactly ONE quote per canonical instrument.

Rules (locked):
  * a quote is *fresh* when its ``received_ts`` age is within
    ``stale_after_seconds``; stale-only instruments are UNAVAILABLE and are
    never presented as a live selection;
  * among fresh candidates the MOST RECENT quote wins; provider preference
    is a TIE-BREAK ONLY (never a routing gate);
  * a quote without a ``canonical_id`` is not selectable;
  * no second store is created and the write-path key is untouched.

Provider inference from the instrument-key format is the project's existing
convention (pipe -> upstox, colon/numeric -> fyers); it is injectable so the
selection policy stays testable and broker-neutral.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from market.models import Quote

DEFAULT_STALE_SECONDS = 300.0
DEFAULT_PREFERENCE: tuple[str, ...] = ("upstox", "fyers")


def provider_of_key(instrument_token: str) -> str:
    """Infer the provider from an instrument-key format (existing convention)."""
    if "|" in instrument_token:
        return "upstox"
    if ":" in instrument_token or instrument_token.isdigit():
        return "fyers"
    return "unknown"


@dataclass(frozen=True)
class SelectedQuote:
    """One selected quote plus its selection metadata."""

    quote: Quote
    selected_from: str
    stale: bool
    candidate_count: int


def _age_seconds(quote: Quote, now: datetime) -> float | None:
    ts = quote.received_ts
    if ts is None:
        return None
    return (now - ts).total_seconds()


def select_quote(
    candidates: Iterable[Quote],
    *,
    preference: Sequence[str] = DEFAULT_PREFERENCE,
    stale_after_seconds: float = DEFAULT_STALE_SECONDS,
    provider_of: Callable[[str], str] = provider_of_key,
    now: datetime | None = None,
) -> SelectedQuote | None:
    """Select ONE fresh quote from ``candidates`` (None when unavailable)."""
    moment = now or datetime.now(timezone.utc)
    fresh: list[Quote] = []
    for q in candidates:
        age = _age_seconds(q, moment)
        # A quote whose age cannot be established is NOT fresh: the
        # selection fails closed (an unknown age is never evidence of
        # live data).
        if age is not None and age <= stale_after_seconds:
            fresh.append(q)
    if not fresh:
        return None

    pref = list(preference)

    def rank(q: Quote) -> tuple[float, int]:
        # Primary: most recent first (-ts ascending). Secondary: provider
        # preference (tie-break only).
        provider = provider_of(q.instrument_token)
        try:
            order = pref.index(provider)
        except ValueError:
            order = len(pref)
        ts = q.received_ts.timestamp() if q.received_ts is not None else 0.0
        return (-ts, order)

    chosen = sorted(fresh, key=rank)[0]
    return SelectedQuote(
        quote=chosen,
        selected_from=provider_of(chosen.instrument_token),
        stale=False,
        candidate_count=len(fresh),
    )


def select_best_quote(
    candidates: Iterable[Quote],
    *,
    preference: Sequence[str] = DEFAULT_PREFERENCE,
    stale_after_seconds: float = DEFAULT_STALE_SECONDS,
    provider_of: Callable[[str], str] = provider_of_key,
    now: datetime | None = None,
) -> SelectedQuote | None:
    """The best quote for one canonical instrument - fresh OR stale.

    A fresh candidate always wins. When ONLY stale data exists the most
    recent stale candidate is returned with ``stale=True`` so the last
    known value stays visible (with its ``as_of`` timestamp) instead of
    being silently dropped. A stale selection is never presented as live.
    """
    quotes = list(candidates)
    fresh = select_quote(quotes, preference=preference,
                          stale_after_seconds=stale_after_seconds,
                          provider_of=provider_of, now=now)
    if fresh is not None:
        return fresh
    if not quotes:
        return None
    def _rank(q: Quote) -> tuple[float, int]:
        provider = provider_of(q.instrument_token)
        try:
            order = list(preference).index(provider)
        except ValueError:
            order = len(preference)
        ts = q.received_ts.timestamp() if q.received_ts is not None else 0.0
        return (-ts, order)

    chosen = sorted(quotes, key=_rank)[0]
    return SelectedQuote(quote=chosen,
                         selected_from=provider_of(chosen.instrument_token),
                         stale=True, candidate_count=len(quotes))


def select_canonical_quotes(
    quotes: Iterable[Quote],
    *,
    preference: Sequence[str] = DEFAULT_PREFERENCE,
    stale_after_seconds: float = DEFAULT_STALE_SECONDS,
    provider_of: Callable[[str], str] = provider_of_key,
    now: datetime | None = None,
) -> dict[str, SelectedQuote]:
    """Group quotes by ``canonical_id`` and select one per instrument.

    Quotes without a canonical id are skipped. Stale-only instruments are
    omitted (unavailable), never returned as a stale selection.
    """
    groups: dict[str, list[Quote]] = {}
    for q in quotes:
        cid = q.canonical_id
        if not cid:
            continue
        groups.setdefault(cid, []).append(q)
    out: dict[str, SelectedQuote] = {}
    for cid, group in groups.items():
        sel = select_quote(
            group, preference=preference,
            stale_after_seconds=stale_after_seconds,
            provider_of=provider_of, now=now)
        if sel is not None:
            out[cid] = sel
    return out


class QuoteCoordinator:
    """Read-side coordinator over a ``MarketService``-like quote source."""

    def __init__(
        self,
        quote_source: Any,
        *,
        preference: Sequence[str] = DEFAULT_PREFERENCE,
        stale_after_seconds: float = DEFAULT_STALE_SECONDS,
        provider_of: Callable[[str], str] = provider_of_key,
    ) -> None:
        self._quote_source = quote_source
        self._preference = tuple(preference)
        self._stale_after = stale_after_seconds
        self._provider_of = provider_of

    async def _quotes(self) -> tuple[Quote, ...]:
        src = self._quote_source
        quotes = getattr(src, "quotes", None)
        if callable(quotes):
            result = quotes()
            if inspect.isawaitable(result):
                result = await result
            return tuple(result)
        if isinstance(src, dict):
            return tuple(src.values())
        return tuple(src or ())

    async def get_canonical_quote(self, canonical_id: str) -> SelectedQuote | None:
        """One fresh selected quote for ``canonical_id`` (None if unavailable)."""
        group = [q for q in await self._quotes() if q.canonical_id == canonical_id]
        return select_quote(
            group, preference=self._preference,
            stale_after_seconds=self._stale_after,
            provider_of=self._provider_of)

    async def canonical_quotes(self) -> dict[str, SelectedQuote]:
        """Selected quote per canonical instrument (snapshot-consistent)."""
        return select_canonical_quotes(
            await self._quotes(), preference=self._preference,
            stale_after_seconds=self._stale_after,
            provider_of=self._provider_of)


def selected_quote_to_dict(sel: SelectedQuote) -> dict[str, Any]:
    """Serialize a selected quote with its selection metadata."""
    from market.serialization import quote_to_dict
    data = quote_to_dict(sel.quote)
    data["selected_from"] = sel.selected_from
    data["stale"] = sel.stale
    data["candidate_count"] = sel.candidate_count
    return data
