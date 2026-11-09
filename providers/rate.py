"""Central request-budget manager (broker-neutral).

Every REST call to a provider consumes a documented budget:

* requests per second,
* requests per minute,
* requests per day (or per 30 minutes, where that is the documented
  window).

The budget belongs to the PROVIDER and is shared by ALL callers of the
same API family (quotes, depth, history, option chain) - one bucket per
provider, not one per module.

Behaviour:

* a request is refused when its window is exhausted (the caller gets a
  clear "budget exhausted" answer instead of a 429 that would count
  against the provider's block threshold);
* a 429 from the provider stops that provider's traffic for the window
  (never retried into a documented block);
* nothing is polled speculatively: a request only happens when a caller
  asks for data.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from providers.capacity import rest_limits


class BudgetExhausted(RuntimeError):
    """The provider's documented request budget for this window is used up."""

    def __init__(self, provider: str, window: str) -> None:
        super().__init__(f"{provider} request budget exhausted ({window})")
        self.provider = provider
        self.window = window


@dataclass
class WindowCounter:
    """A sliding window counter (per second / minute / day)."""

    limit: int | None
    window_seconds: float
    events: deque[float] = field(default_factory=deque)

    def allow(self, now: float) -> bool:
        if not self.limit:
            return True
        cutoff = now - self.window_seconds
        while self.events and self.events[0] < cutoff:
            self.events.popleft()
        return len(self.events) < self.limit

    def record(self, now: float) -> None:
        if self.limit:
            self.events.append(now)

    def used(self, now: float) -> int:
        cutoff = now - self.window_seconds
        return sum(1 for ts in self.events if ts >= cutoff)


@dataclass
class ProviderBudget:
    """One DOCUMENTED quota bucket (provider + quota scope)."""

    provider: str
    bucket: str
    per_second: WindowCounter
    per_minute: WindowCounter
    per_day: WindowCounter
    #: seconds during which the provider is considered blocked after a 429
    blocked_until: float = 0.0
    breaches: int = 0

    def blocked(self, now: float) -> bool:
        return now < self.blocked_until

    def describe(self, now: float | None = None) -> dict[str, Any]:
        moment = now if now is not None else time.monotonic()
        return {
            "provider": self.provider,
            "bucket": self.bucket,
            "per_second": {"limit": self.per_second.limit,
                           "used": self.per_second.used(moment)},
            "per_minute": {"limit": self.per_minute.limit,
                           "used": self.per_minute.used(moment)},
            "per_day": {"limit": self.per_day.limit,
                        "used": self.per_day.used(moment)},
            "blocked": self.blocked(moment),
            "breaches": self.breaches,
        }


class RequestBudgetManager:
    """Shared request budgets, one per DOCUMENTED quota scope.

    A budget is keyed by ``(provider, bucket)`` where the bucket is the
    quota scope the provider documents:

    * FYERS documents ONE shared pool for its Data APIs (quotes, depth,
      history, option chain, market status, symbol-token) - they all
      draw from the same "data" bucket;
    * Upstox documents its limits "per API, per user", so each API family
      (market quote, historical, option chain, market info) and the
      stricter TOTP/token row are SEPARATE buckets. Combining them would
      wrongly throttle one endpoint because another was busy, and would
      apply the wrong ceiling to the auth endpoints.

    Callers of the same documented quota share the accounting state;
    unrelated quotas never do.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._budgets: dict[tuple[str, str], ProviderBudget] = {}

    def _budget(self, provider: str, bucket: str = "default") -> ProviderBudget:
        key = (provider, bucket)
        budget = self._budgets.get(key)
        if budget is not None:
            return budget
        limits = rest_limits(provider, bucket)
        per_day_limit = limits.get("requests_per_day")
        per_30 = limits.get("requests_per_30_minutes")
        if per_day_limit is None and per_30:
            # The documented 30-minute window is enforced as a rolling
            # window of the same length (the stricter of the two applies).
            per_day_limit = int(per_30)
            day_window = 30 * 60.0
        else:
            day_window = 24 * 3600.0
        budget = ProviderBudget(
            provider=provider,
            bucket=bucket,
            per_second=WindowCounter(limits.get("requests_per_second"), 1.0),
            per_minute=WindowCounter(limits.get("requests_per_minute"), 60.0),
            per_day=WindowCounter(per_day_limit, day_window),
        )
        self._budgets[key] = budget
        return budget

    def allow(self, provider: str, bucket: str = "default") -> bool:
        """True when a request may be made right now."""
        now = self._clock()
        with self._lock:
            budget = self._budget(provider, bucket)
            if budget.blocked(now):
                return False
            return (budget.per_second.allow(now)
                    and budget.per_minute.allow(now)
                    and budget.per_day.allow(now))

    def record(self, provider: str, bucket: str = "default") -> None:
        """Record one request against the provider's budget."""
        now = self._clock()
        with self._lock:
            budget = self._budget(provider, bucket)
            budget.per_second.record(now)
            budget.per_minute.record(now)
            budget.per_day.record(now)

    def acquire(self, provider: str, bucket: str = "default") -> None:
        """Consume one request slot (raises when the budget is exhausted)."""
        now = self._clock()
        with self._lock:
            budget = self._budget(provider, bucket)
            if budget.blocked(now):
                raise BudgetExhausted(provider, "blocked")
            for name, counter in (("second", budget.per_second),
                                   ("minute", budget.per_minute),
                                   ("day", budget.per_day)):
                if not counter.allow(now):
                    raise BudgetExhausted(provider, name)
            budget.per_second.record(now)
            budget.per_minute.record(now)
            budget.per_day.record(now)

    def report_block(self, provider: str, bucket: str = "default", *,
                     seconds: float = 60.0) -> None:
        """Record a provider rate-limit response (429) for one quota bucket.

        The traffic of that bucket stops for the window: retrying into a
        documented block is worse than waiting.
        """
        now = self._clock()
        with self._lock:
            budget = self._budget(provider, bucket)
            budget.breaches += 1
            limits = rest_limits(provider)
            before_block = limits.get("per_minute_breaches_before_block")
            if before_block is not None and \
                    budget.breaches >= int(before_block):
                # FYERS blocks the account for the rest of the day after
                # repeated per-minute breaches - stop entirely.
                budget.blocked_until = now + 24 * 3600.0
            else:
                # Otherwise wait for the current window to roll over.
                budget.blocked_until = max(budget.blocked_until,
                                           now + seconds)

    def reset(self) -> None:
        """Clear every provider budget (used to isolate test runs)."""
        with self._lock:
            self._budgets.clear()

    def describe(self) -> dict[str, Any]:
        """The state of every quota bucket, keyed by provider and bucket."""
        now = self._clock()
        with self._lock:
            return {f"{pid}:{bucket}": budget.describe(now)
                    for (pid, bucket), budget in self._budgets.items()}


def budgeted_transport(provider: str, transport: Callable[..., Any],
                       *, bucket: str = "default",
                       manager: RequestBudgetManager | None = None,
                       ) -> Callable[..., Any]:
    """Wrap a sync HTTP transport with the provider's shared budget.

    EVERY request of the provider goes through this wrapper, so all
    callers (quotes, depth, history, option chain, symbol-token, index
    fallback) share ONE accounting state. The budget is checked BEFORE
    the request: an exhausted window never reaches the network, so the
    provider is never pushed into a block by our own retries.
    """
    mgr = manager or get_budget_manager()

    def _wrapped(method: str, url: str, headers: Any, body: Any,
                 timeout: float) -> Any:
        mgr.acquire(provider, bucket)
        try:
            return transport(method, url, headers, body, timeout)
        except Exception:
            raise

    return _wrapped


#: Process-wide manager (the budgets belong to the provider, not to a
#: module).
_DEFAULT: RequestBudgetManager | None = None


def get_budget_manager() -> RequestBudgetManager:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = RequestBudgetManager()
    return _DEFAULT


def set_budget_manager(manager: RequestBudgetManager | None) -> None:
    global _DEFAULT
    _DEFAULT = manager
