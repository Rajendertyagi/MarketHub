"""Verified provider capacity, entitlement and request budgets.

Capacity is a **CEILING, never a target**. This module owns the single
authoritative model of "how many instruments may this provider carry",
derived from:

* the adapter's DECLARED documented limits (per feed mode, per account
  tier, per combined-category rule);
* the account's ACTUAL tier, when it can be verified (an operator
  declaration or an observed broker behaviour). The default tier is used
  otherwise and is reported as such - a paid tier is never assumed;
* the provider's documented connection/account scope.

There is no global application cap and no blanket reserve. A provider may
declare a headroom, but only with documented evidence (see
``ProviderCapabilities.headroom``), and it is reported in the projection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from providers.capabilities import effective_budget

#: Entitlement sources, ordered by trust.
SOURCE_VERIFIED = "verified"            # observed / operator-confirmed
SOURCE_OPERATOR = "operator_declared"   # configured by the operator
SOURCE_DEFAULT = "declared_default"     # the documented default tier

#: Plan overrides are NO LONGER hard-coded here: the tiers live in the
#: adapter declarations (``tier_limits``). This mapping only exists for
#: backwards compatibility with older imports.
PLAN_CAP_OVERRIDES: dict[str, dict[str, int]] = {}


@dataclass(frozen=True)
class ProviderEntitlement:
    """What a provider is actually allowed to do right now."""

    provider: str
    tier: str
    symbols: int
    source: str
    mode: str = "full"
    verified_at: str | None = None
    note: str = ""

    @property
    def is_verified(self) -> bool:
        return self.source in (SOURCE_VERIFIED, SOURCE_OPERATOR)

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "tier": self.tier,
            "symbols": self.symbols,
            "source": self.source,
            "verified": self.is_verified,
            "verified_at": self.verified_at,
            "mode": self.mode,
            "note": self.note,
        }


def _capabilities(provider: str) -> Any:
    from providers.registry import get_registry
    return get_registry().get(provider)


def resolve_entitlement(provider: str, *, tier: str | None = None,
                         mode: str | None = None,
                         source: str | None = None,
                         verified_at: str | None = None,
                         caps: Any = None,
                         ) -> ProviderEntitlement:
    """The effective entitlement of a provider.

    ``tier`` may be given by the operator (source ``operator_declared``)
    or observed by the runtime (source ``verified``). Without it the
    provider's documented DEFAULT tier applies and is reported as
    ``declared_default`` - the uncertainty is never hidden.
    """
    if caps is None:
        caps = _capabilities(provider)
    if caps is None:
        return ProviderEntitlement(provider=provider, tier="unknown",
                                   symbols=0, source=SOURCE_DEFAULT,
                                   note="provider not registered")
    active_mode = mode or caps.default_mode
    default_tier = getattr(caps, "default_tier", "standard")
    declared = caps.tier_limits or {}
    if tier:
        active_tier = str(tier).lower()
        src = source or SOURCE_OPERATOR
        note = ""
    else:
        active_tier = default_tier
        src = source or SOURCE_DEFAULT
        note = ("account tier not verified - the documented default tier "
                "applies")
    mode_limit = int(caps.mode_limit(active_mode))
    if active_tier in declared:
        tier_limit = int(declared[active_tier])
        # The feed mode in use narrows the account ceiling; an explicitly
        # declared tier is the account's own limit.
        symbols = tier_limit if tier else min(tier_limit, mode_limit)
    else:
        symbols = mode_limit
        note = (note + "; " if note else "") + \
            f"tier '{active_tier}' is not documented - the feed-mode limit applies"
    # A documented provider-specific headroom (rare, evidence-backed).
    headroom = int(getattr(caps, "headroom", 0) or 0)
    if headroom:
        symbols = max(0, symbols - headroom)
    return ProviderEntitlement(provider=provider, tier=active_tier,
                               symbols=effective_budget(symbols),
                               source=src, mode=active_mode,
                               verified_at=verified_at, note=note)


def provider_budget(provider: str, *, tier: str | None = None,
                    mode: str | None = None, caps: Any = None) -> int:
    """The instrument ceiling for a provider (never a target)."""
    return resolve_entitlement(provider, tier=tier, mode=mode,
                               caps=caps).symbols


def plan_capacity(provider: str, *, plan: str | None = None,
                  default: int | None = None) -> int:
    """Backwards-compatible helper: the capacity for a provider/plan."""
    if plan:
        overrides = PLAN_CAP_OVERRIDES.get(provider, {})
        key = str(plan).strip().lower()
        if key in overrides:
            return int(overrides[key])
        try:
            return int(str(plan))
        except (TypeError, ValueError):
            pass
    if default is not None:
        return int(default)
    return provider_budget(provider)


def rest_limits(provider: str, bucket: str | None = None) -> dict[str, Any]:
    """The provider's documented REST limits for one quota bucket.

    The adapter declares the buckets it documents:

    * FYERS: one shared "data" pool (quotes, depth, history, option
      chain, market status, symbol-token) plus, optionally, a separate
      transaction/user-info pool;
    * Upstox: one bucket per API family (the limits are documented
      "per API, per user") and a separate, stricter bucket for the
      TOTP/token row.

    With ``bucket=None`` the whole mapping is returned.
    """
    caps = _capabilities(provider)
    if caps is None:
        return {}
    declared = dict(getattr(caps, "rest_limits", {}) or {})
    if bucket is None:
        return declared
    spec = declared.get(bucket)
    if spec is None:
        # A flat declaration (one bucket for the whole provider).
        if declared and "requests_per_second" in declared:
            return dict(declared)
        return {}
    return dict(spec)


def max_keys_per_request(provider: str, bucket: str | None = None
                         ) -> int | None:
    """The provider's documented batch size for quote-style requests."""
    limits = rest_limits(provider, bucket)
    for key in ("max_instrument_keys_per_request", "max_symbols_per_request"):
        if key in limits:
            return int(limits[key])
    return None
