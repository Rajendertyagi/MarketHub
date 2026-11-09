"""Deterministic provider routing for canonical contracts.

Routing rules (documented, deterministic - never random, never a quota):

1. Only providers that actually LIST the contract (catalogue) and
   declare the required capabilities can serve it.
2. A contract that only ONE provider lists is routed to that provider -
   unique coverage wins, even if another provider is preferred.
3. Otherwise the contract is routed to the first enabled provider in the
   registry's configuration order that still has capacity.
4. A contract is routed to ONE provider at a time. A second provider is
   used only as a FAILOVER target when the selected provider cannot
   serve the contract (not running, not healthy, no usable data).

The catalogue is the authority on provider identifiers: a provider-native
storage key is never mistaken for a canonical identity, and a static table
is only a fallback for a contract the catalogue does not list.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("event_server")


@dataclass(frozen=True)
class ProviderOffer:
    """One provider's offer to serve a canonical contract."""

    provider: str
    key: str
    canonical_id: str | None = None
    #: can the provider serve this contract over its market-data STREAM?
    streamable: bool = True


def _stream_supported(provider: str, row: dict[str, Any]) -> bool:
    """Whether a provider can serve a catalogue row over its STREAM.

    A provider that lists a contract but cannot STREAM it must not be
    given the subscription: FYERS, for example, has no official HSM
    index topic for every index it quotes over REST (NIFTYFPI is the
    known case), so such an index is served over the REST fallback, not
    through the socket.
    """
    if provider == "fyers":
        if str(row.get("instrument_type") or "").upper() == "INDEX":
            from brokers.fyers.hsm_symbols import INDEX_TOPICS
            return str(row.get("tradingsymbol") or "") in INDEX_TOPICS
    return True


def catalogue_offers(catalog: Any, canonical_id: str,
                     ) -> list[ProviderOffer]:
    """Every provider that lists the contract, with its feed/storage key.

    The catalogue is the authority: it records which provider published
    which identifier. A contract the catalogue does not know yields no
    offers (never an invented key).
    """
    if catalog is None or not canonical_id:
        return []
    try:
        from app.canonical_identity import provider_quote_key
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    try:
        finder = getattr(catalog, "find_canonical_rows", None)
        if callable(finder):
            rows = list(finder(canonical_id) or [])
        else:
            rows = list(catalog.search(canonical_id=canonical_id, limit=50)
                        or [])
    except Exception:
        rows = []
    offers: list[ProviderOffer] = []
    seen: set[str] = set()
    for row in rows:
        try:
            key = provider_quote_key(row)
        except Exception:
            key = None
        provider = str(row.get("provider") or "")
        if not provider or not key or provider in seen:
            continue
        seen.add(provider)
        offers.append(ProviderOffer(provider=provider, key=str(key),
                                    canonical_id=canonical_id,
                                    streamable=_stream_supported(provider,
                                                               row)))
    return offers


def order_providers(registry: Any, offers: list[ProviderOffer],
                    ) -> list[ProviderOffer]:
    """Order the offers by the registry's configuration order."""
    try:
        order = list(registry.preference_order())
    except Exception:
        order = []
    try:
        enabled = set(registry.enabled_ids())
    except Exception:
        enabled = set()

    def _rank(offer: ProviderOffer) -> tuple[int, int]:
        if offer.provider not in enabled:
            return (2, len(order))
        try:
            return (0, order.index(offer.provider))
        except ValueError:
            return (1, len(order))

    return sorted(offers, key=_rank)


def choose_offer(offers: list[ProviderOffer], *,
                 assigned: dict[str, int] | None = None,
                 limits: dict[str, int | None] | None = None,
                 preference: Sequence[str] = (),
                 ) -> ProviderOffer | None:
    """Pick the offer with the LOWEST PROJECTED UTILIZATION.

    The objective: both providers should carry useful work. A shared
    contract therefore goes to the provider that is proportionally
    LESS loaded (assigned / verified capacity), not automatically to the
    provider with the most spare slots - otherwise the larger provider
    takes every shared instrument and the smaller one stays idle.

    The split is a CONSEQUENCE of the documented capacities and the
    actual demand, not a quota, a percentage or a random choice: with
    identical capacities and demand both providers are used equally; the
    tie is broken by the configuration order.
    """
    assigned = assigned or {}
    limits = limits or {}
    order = list(preference)
    if not offers:
        return None

    def _key(offer: ProviderOffer) -> tuple[float, int]:
        limit = limits.get(offer.provider)
        used = assigned.get(offer.provider, 0)
        utilization = ((used + 1) / limit) if limit else float("inf")
        try:
            rank = order.index(offer.provider)
        except ValueError:
            rank = len(order)
        return (utilization, rank)

    return min(offers, key=_key)


def select_offer(offers: list[ProviderOffer], *,
                 capacity_left: dict[str, int] | None = None,
                 ) -> ProviderOffer | None:
    """Pick the provider that serves the contract.

    The rule is deterministic and capacity-aware:

    * **unique coverage** wins - a contract only one provider lists goes
      there, whatever the preference order says;
    * otherwise the provider with the **most remaining verified capacity**
      takes it (so shared demand follows the capacity, and a provider
      that is close to its ceiling is relieved instead of being pushed
      over it); ties are broken by the configuration order;
    * with no capacity information, the first configured provider wins.

    The capacity is a CEILING, never a target: a provider with room is
    not given work it does not need, and no provider is given a second
    copy of a contract another provider already serves.
    """
    if not offers:
        return None
    ordered = order_providers(_registry_of(offers), offers)
    if len(ordered) == 1:
        return ordered[0]
    if capacity_left:
        usable = [o for o in ordered
                  if capacity_left.get(o.provider) is None
                  or capacity_left.get(o.provider, 0) > 0]
        if not usable:
            return None
        # Most remaining capacity first; configuration order breaks ties.
        best = max(
            usable,
            key=lambda o: (capacity_left.get(o.provider, 0)
                            if capacity_left.get(o.provider) is not None
                            else 0,
                           -ordered.index(o)))
        return best
    return ordered[0]


def _registry_of(offers: list[ProviderOffer]) -> Any:
    """The default provider registry (broker-neutral)."""
    try:
        from providers.registry import get_registry
        return get_registry()
    except Exception:
        return None


def failover_offer(offers: list[ProviderOffer], current: str | None,
                    *, capacity_left: dict[str, int] | None = None,
                    ) -> ProviderOffer | None:
    """The best FAILOVER target for a contract (a different provider).

    Used when the selected provider cannot serve the contract (offline,
    unhealthy, no usable data). The replacement is chosen with the same
    deterministic rules.
    """
    others = [o for o in offers if o.provider != current]
    if not others:
        return None
    return select_offer(others, capacity_left=capacity_left)
