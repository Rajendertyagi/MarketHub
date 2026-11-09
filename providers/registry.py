"""Provider registry — single source of provider identity/capability/capacity.

The registry aggregates adapter-declared :class:`ProviderCapabilities` (each
broker package owns its own declaration). It is broker-neutral: no broker
behaviour lives here, only the metadata consumers need to route data.

Single source of truth for:
  * which providers exist / are enabled
  * what each provider can supply (capability tokens)
  * each provider's capacity (broker limit, from its own declaration)
  * provider inference from an instrument key (key style)
  * provider preference order (configurable; default = declared order)

The default registry is built lazily from the adapter declarations, so
importing this module never imports broker code.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from providers.capabilities import (
    STYLE_COLON,
    STYLE_NUMERIC,
    STYLE_PIPE,
    ProviderCapabilities,
)

_STYLE_MATCHERS: dict[str, Callable[[str], bool]] = {
    STYLE_PIPE: lambda k: "|" in k,
    STYLE_COLON: lambda k: ":" in k,
    STYLE_NUMERIC: lambda k: k.isdigit(),
}


class ProviderRegistry:
    """Broker-neutral provider metadata registry (order = preference)."""

    def __init__(
        self,
        providers: Sequence[ProviderCapabilities] = (),
        *,
        enabled: Iterable[str] | None = None,
        preference: Sequence[str] | None = None,
        status_fn: Callable[[str], str | None] | None = None,
        plan_caps: Mapping[str, int] | None = None,
    ) -> None:
        self._providers: dict[str, ProviderCapabilities] = {
            p.provider: p for p in providers}
        self._order: list[str] = [p.provider for p in providers]
        self._enabled: set[str] = (set(enabled) if enabled is not None
                                   else set(self._order))
        self._preference: list[str] = (list(preference) if preference
                                       else list(self._order))
        self._status_fn = status_fn
        # Config-driven per-provider plan caps (e.g. fyers.plan_cap).
        self._plan_caps: dict[str, int] = dict(plan_caps or {})

    # -- identity -----------------------------------------------------------

    def ids(self) -> list[str]:
        return list(self._order)

    def all(self) -> list[ProviderCapabilities]:
        return [self._providers[p] for p in self._order]

    def get(self, provider: str) -> ProviderCapabilities | None:
        return self._providers.get(provider)

    def enabled_ids(self) -> list[str]:
        return [p for p in self._order if p in self._enabled]

    def is_enabled(self, provider: str) -> bool:
        return provider in self._enabled

    def preference_order(self) -> list[str]:
        return list(self._preference)

    # -- capability / capacity ---------------------------------------------

    def providers_with(self, *caps: str) -> list[str]:
        """Providers that declare ALL of ``caps``, in preference order."""
        order = self._preference or self._order
        return [p for p in order
                if p in self._providers and self._providers[p].supports(*caps)]

    def capacity(self, provider: str) -> int | None:
        p = self._providers.get(provider)
        return p.capacity if p is not None else None

    def mode_limit(self, provider: str, mode: str) -> int | None:
        p = self._providers.get(provider)
        return p.mode_limit(mode) if p is not None else None

    def budget(self, provider: str, *, tier: str | None = None) -> int | None:
        """The VERIFIED subscription ceiling of a provider.

        This is a CEILING, not a target: it is the provider's documented
        limit for the account's tier (the documented default tier unless
        the tier was verified or declared by the operator). There is no
        application-wide cap and no blanket reserve.
        """
        caps = self._providers.get(provider)
        if caps is None:
            return None
        from providers.capacity import resolve_entitlement
        return resolve_entitlement(provider, tier=tier, caps=caps).symbols

    def entitlement(self, provider: str, *,
                    tier: str | None = None) -> Any:
        """The full entitlement record (tier, source, verified flag)."""
        caps = self._providers.get(provider)
        if caps is None:
            return None
        from providers.capacity import resolve_entitlement
        return resolve_entitlement(provider, tier=tier, caps=caps)

    def mode_budget(self, provider: str, mode: str, *,
                    tier: str | None = None) -> int | None:
        """The ceiling for one feed mode (mode limit within the tier)."""
        p = self._providers.get(provider)
        if p is None:
            return None
        from providers.capacity import resolve_entitlement
        ent = resolve_entitlement(provider, tier=tier, mode=mode, caps=p)
        return min(int(p.mode_limit(mode)), int(ent.symbols))

    # -- inference / status -------------------------------------------------

    def infer(self, instrument_key: str, *, default: str | None = None) -> str | None:
        """Infer the provider from an instrument key's style.

        The key STYLE is declared by each adapter (``key_styles``) — this is
        the registry-driven fallback used when the catalogue does not know
        the key (e.g. a freshly configured instrument not yet synced).
        """
        if instrument_key:
            for pid in self._order:
                p = self._providers[pid]
                if any(_STYLE_MATCHERS.get(style, _never)(instrument_key)
                       for style in p.key_styles):
                    return pid
        if default is not None:
            return default
        return self._order[0] if self._order else None

    def provider_for_key(
        self, instrument_key: str, *, catalog: Any = None,
        default: str | None = None,
    ) -> str | None:
        """Resolve the provider that owns an instrument key.

        The CATALOGUE is authoritative: it records which provider published
        each key. The adapter-declared key STYLE is only the fallback for a
        key the catalogue does not know (never a string-shape guess when the
        catalogue can answer).
        """
        if not instrument_key:
            return default
        if catalog is not None:
            try:
                row = catalog.get_by_key(instrument_key)
            except Exception:
                row = None
            if row:
                provider = row.get("provider")
                if provider:
                    return str(provider)
        return self.infer(instrument_key, default=default)

    def status(self, provider: str) -> str | None:
        return self._status_fn(provider) if self._status_fn is not None else None

    # -- projection ---------------------------------------------------------

    def projection(self) -> list[dict[str, Any]]:
        """Broker-neutral view for the API/WebUI (no secrets)."""
        out = []
        for pid in self._order:
            p = self._providers[pid]
            ent = None
            try:
                ent = self.entitlement(pid)
            except Exception:
                ent = None
            out.append({
                "provider": p.provider,
                "label": p.label,
                "enabled": pid in self._enabled,
                "capabilities": sorted(p.capabilities),
                "modes": dict(p.modes),
                "default_mode": p.default_mode,
                "capacity": p.capacity,
                # The verified ceiling and where it comes from (never
                # hidden): tier, source and whether it is verified.
                "entitlement": ent.describe() if ent else None,
                "status": self.status(pid),
            })
        return out


def _never(_key: str) -> bool:
    return False


# ---------------------------------------------------------------------------
# Default registry (built lazily from adapter declarations)
# ---------------------------------------------------------------------------

_DEFAULT: ProviderRegistry | None = None


def _load_declarations() -> list[ProviderCapabilities]:
    """Import each adapter's capability declaration (lazy, broker-neutral)."""
    from brokers.fyers.capabilities import CAPABILITIES as fyers_caps
    from brokers.upstox.capabilities import CAPABILITIES as upstox_caps
    return [upstox_caps, fyers_caps]


def get_registry() -> ProviderRegistry:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = ProviderRegistry(_load_declarations())
    return _DEFAULT


def set_registry(registry: ProviderRegistry) -> None:
    """Install a configured registry (composition / tests)."""
    global _DEFAULT
    _DEFAULT = registry
