"""Canonical provider capability vocabulary + descriptor (broker-neutral).

This module defines the shared *vocabulary* (capability tokens and the
descriptor type) only. Concrete declarations live WITH each broker adapter
(e.g. ``brokers/upstox/capabilities.py``) so there is exactly one source of
truth per provider and no separate hand-maintained capability table.

No broker code is imported here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# -- capability tokens (data a provider can supply) --------------------------
CAP_LTP = "ltp"
CAP_OHLC = "ohlc"
CAP_OI = "oi"
CAP_PREVIOUS_OI = "previous_oi"
CAP_OI_CHANGE = "oi_change"
CAP_OI_CHANGE_PERCENT = "oi_change_percent"
CAP_DEPTH = "depth"
CAP_SPOT = "spot"
CAP_HISTORY = "history"
CAP_OPTION_CHAIN = "option_chain"
CAP_OI_ANALYTICS = "oi_analytics"
CAP_MAX_PAIN = "max_pain"
CAP_PCR = "pcr"

ALL_CAPABILITIES: frozenset[str] = frozenset({
    CAP_LTP, CAP_OHLC, CAP_OI, CAP_PREVIOUS_OI, CAP_OI_CHANGE,
    CAP_OI_CHANGE_PERCENT, CAP_DEPTH, CAP_SPOT, CAP_HISTORY,
    CAP_OPTION_CHAIN, CAP_OI_ANALYTICS, CAP_MAX_PAIN, CAP_PCR,
})

# Instrument-key style tokens understood by the registry's inference.
STYLE_PIPE = "pipe"       # ``NSE_EQ|INE002A01018`` (Upstox)
STYLE_COLON = "colon"     # ``NSE:RELIANCE-EQ`` (Fyers)
STYLE_NUMERIC = "numeric"  # bare numeric provider token (Fyers)

def effective_budget(capacity: int) -> int:
    """The usable instrument-key budget for a broker capacity.

    The budget IS the verified broker limit: capacity is a CEILING, not a
    target. There is no blanket reserve and no application-wide cap - a
    provider-specific headroom is only ever declared by the adapter (see
    ``ProviderCapabilities.headroom``) when there is documented evidence
    that the provider requires it.
    """
    return max(0, int(capacity))


@dataclass(frozen=True)
class ProviderCapabilities:
    """One provider's declared capabilities, capacity and key style.

    ``capacity`` is derived from the ``default_mode`` limit — never a second
    hand-maintained number.
    """

    provider: str
    label: str
    capabilities: frozenset[str]
    modes: Mapping[str, int]
    default_mode: str
    key_styles: tuple[str, ...]
    #: account tier -> documented simultaneous-symbol ceiling
    tier_limits: Mapping[str, int] = field(default_factory=dict)
    #: the tier that applies when the account tier is NOT verified
    default_tier: str = "standard"
    #: feed category -> ceiling when several categories are combined
    combined_limits: Mapping[str, int] = field(default_factory=dict)
    #: documented REST request-rate / batch limits
    rest_limits: Mapping[str, Any] = field(default_factory=dict)
    #: documented provider-specific headroom (usually none)
    headroom: int = 0

    @property
    def capacity(self) -> int:
        """Max simultaneous instrument keys in the default mode."""
        return int(self.modes.get(self.default_mode, 0))

    def supports(self, *caps: str) -> bool:
        return set(caps) <= set(self.capabilities)

    def mode_limit(self, mode: str) -> int:
        return int(self.modes.get(mode, self.capacity))

    def combined_limit(self, mode: str) -> int:
        """Ceiling for ``mode`` when several categories are subscribed."""
        return int(self.combined_limits.get(mode, self.mode_limit(mode)))

    def tier_limit(self, tier: str) -> int:
        """Documented symbol ceiling of an account tier."""
        return int(self.tier_limits.get(str(tier).lower(),
                                         self.mode_limit(self.default_mode)))
