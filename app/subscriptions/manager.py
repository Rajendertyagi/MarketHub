"""Demand-driven, lease-based subscription manager.

A **lease** is one consumer's need for one instrument. Leases are
reference-counted: an instrument stays subscribed while at least one
lease exists, and no consumer can remove another's instrument.

The manager is the single owner of:

* the DESIRED set per provider - the union of the leases routed there.
  Nothing is ever subscribed because capacity happens to be free;
* the ALLOCATION against the provider's verified ceiling: leases are
  admitted in priority order, lower-priority leases are released safely
  before a higher-priority one is admitted, and whatever cannot be
  served is reported explicitly (never dropped silently);
* the RUNTIME STATE of every instrument: ``connected``, ``subscribed``,
  ``live``, ``stale``, ``provider_unavailable``, ``capacity_limited`` and
  ``unrouted`` - each with its reason;
* the FAILOVER: when the selected provider cannot serve a contract, the
  contract is re-routed to another enabled provider that lists it, and
  the affected consumers are notified.

Priority (higher value = more important, evicted last):

    REQUIRED  baseline coverage (canonical index spots, nearest futures)
    WORKSPACE the focused instruments an open workspace needs
    ALERT     the dependencies of an enabled alert
    OTHER     analytics universe, watchlists, ad-hoc requests
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("event_server")

#: Priority values (higher = more important).
PRIORITY_OTHER = 10
PRIORITY_ALERT = 20
PRIORITY_WORKSPACE = 30
PRIORITY_REQUIRED = 40

#: Owner kinds (who holds a lease).
OWNER_REQUIRED = "required"
OWNER_WORKSPACE = "workspace"
OWNER_ALERT = "alert"
OWNER_OTHER = "other"

#: Runtime states.
STATE_CONNECTED = "connected"
STATE_SUBSCRIBED = "subscribed"
STATE_LIVE = "live"
STATE_STALE = "stale"
STATE_UNAVAILABLE = "provider_unavailable"
STATE_CAPACITY = "capacity_limited"
STATE_UNROUTED = "unrouted"
STATE_RELEASED = "released"

#: Feed states that mean the provider cannot serve a subscription now.
_UNAVAILABLE_STATES = frozenset({
    "stopped", "not_started", "not started", "auth_required",
    "unauthorized", "unauthenticated", "failed", "error", "disconnected",
    "not_configured", "disabled", "inactive", "no_session", "login_required",
})

#: Feed states that CHANGE what the manager may allocate. A transient
#: transition ("connecting", "reconnecting", "authorizing") does not: the
#: provider's availability is unchanged, so a reconciliation would only
#: repeat the previous answer (and a flapping socket would turn into a
#: reconcile storm).
_SIGNIFICANT_STATES = frozenset({
    "streaming", "connected", "subscribed", "live", "stopped",
    "auth_required", "unauthorized", "unauthenticated", "failed", "error",
    "disconnected", "not_configured", "disabled", "inactive", "no_session",
    "login_required", "not_started",
})


@dataclass
class Lease:
    """One consumer's need for one instrument."""

    lease_id: str
    key: str
    provider: str | None = None
    canonical_id: str | None = None
    priority: int = PRIORITY_OTHER
    owner_kind: str = OWNER_OTHER
    owner_id: str = ""
    state: str = STATE_SUBSCRIBED
    reason: str = ""
    #: True when the caller pinned the provider (the allocation must not
    #: move it); False when the provider is the ALLOCATION's decision.
    pinned: bool = False
    #: The provider/key the DEMAND asked for. A failover moves the lease to
    #: another provider (the key follows the provider); this remembers where
    #: the lease came from so it can go BACK when the demand's provider
    #: becomes available again.
    origin_provider: str | None = None
    origin_key: str | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "key": self.key,
            "provider": self.provider,
            "canonical_id": self.canonical_id,
            "priority": self.priority,
            "owner_kind": self.owner_kind,
            "owner_id": self.owner_id,
            "state": self.state,
            "reason": self.reason,
        }


#: The process-wide subscription manager. The composition root creates
#: exactly one; the subscription service applies the demand THROUGH it, so
#: the subscribed set has a single owner (capacity, availability and
#: failover aware) and is never written twice.
_ACTIVE_MANAGER: SubscriptionManager | None = None


def get_active_manager() -> SubscriptionManager | None:
    """The active subscription manager, or None when none was created."""
    return _ACTIVE_MANAGER


@dataclass
class ProviderPlan:
    """The allocation for one provider."""

    provider: str
    admitted: list[str] = field(default_factory=list)
    capacity_limited: list[dict[str, Any]] = field(default_factory=list)
    #: Demand that could not be served because the PROVIDER is not
    #: reachable (disconnected, unauthenticated, not configured). This is
    #: NOT capacity exhaustion - the two are reported separately so a
    #: broken provider is never mistaken for a full one.
    provider_unavailable: list[dict[str, Any]] = field(default_factory=list)
    budget: int | None = None
    tier: str = ""
    source: str = ""
    verified: bool = False
    unreachable: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "subscribed": len(self.admitted),
            "budget": self.budget,
            "tier": self.tier,
            "entitlement_source": self.source,
            "entitlement_verified": self.verified,
            "capacity_limited": list(self.capacity_limited),
            "provider_unavailable": list(self.provider_unavailable),
            "unrouted": list(self.unreachable),
        }


class SubscriptionManager:
    """Lease registry, allocator and reconciler for market-data subscriptions."""

    #: Minimum delay between two reconciliations triggered by the same
    #: provider (a flapping socket must not cause a reconcile storm).
    _RECONCILE_DEBOUNCE_S = 8.0

    def __init__(
        self,
        *,
        catalog: Any = None,
        registry: Any = None,
        feed_provider: Callable[[str], Any] | None = None,
        status_provider: Callable[[str], str | None] | None = None,
        reconcile_debounce_s: float | None = None,
    ) -> None:
        self._catalog = catalog
        self._registry = registry
        self._feed_provider = feed_provider
        self._status_provider = status_provider
        # The debounce is a class default; a test (or an operator) may
        # shorten it. It NEVER drops a state change: a change inside the
        # window schedules a TRAILING reconcile.
        self._reconcile_debounce_s = float(
            reconcile_debounce_s
            if reconcile_debounce_s is not None
            else self._RECONCILE_DEBOUNCE_S)
        self._lock = threading.RLock()
        self._leases: dict[str, Lease] = {}
        self._by_key: dict[str, list[str]] = {}
        self._last_apply: dict[str, Any] = {}
        self._last_data_at: dict[str, str] = {}
        # The leases this manager created from the DEMAND (the lease set
        # is the union of the leases, so the demand is a function of the
        # leases - never of what happened to be subscribed before).
        self._demand_leases: dict[tuple[str, str, str], str] = {}
        # One reconciliation at a time (the manager is the single applier).
        self._reconcile_lock = asyncio.Lock()
        # Wrapped feed state listeners (id(feed) -> wrapper) so a provider
        # that becomes available re-plans the existing demand.
        self._state_hooks: dict[int, Any] = {}
        self._provider_events: list[dict[str, Any]] = []
        self._last_reconcile_at: dict[str, float] = {}
        self._reconcile_tasks: set[Any] = set()
        # The pending TRAILING reconcile per provider (a state change
        # inside the debounce window is deferred, never dropped).
        self._trailing_tasks: dict[str, Any] = {}
        # Catalogue read models (offers, tick metadata) are cached per
        # catalogue epoch: a reconciliation must not turn into hundreds of
        # catalogue queries.
        self._offers_cache: dict[str, list[Any]] = {}
        self._metadata_cache: dict[tuple[str, str], tuple[str, str]] = {}
        self._catalog_epoch: Any = None
        # The manager is the SINGLE owner of the subscribed set: the
        # subscription service exposes the demand to it and does not
        # apply to the feeds itself (two appliers with different views of
        # the demand would fight each other - one unsubscribing what the
        # other subscribed).
        global _ACTIVE_MANAGER
        if _ACTIVE_MANAGER is None:
            _ACTIVE_MANAGER = self

    # -- registry ------------------------------------------------------------

    @property
    def registry(self) -> Any:
        if self._registry is None:
            from providers.registry import get_registry
            self._registry = get_registry()
        return self._registry

    def leases(self) -> list[Lease]:
        with self._lock:
            return list(self._leases.values())

    def acquire(self, key: str = "", *, provider: str | None = None,
                canonical_id: str | None = None,
                priority: int = PRIORITY_OTHER,
                owner_kind: str = OWNER_OTHER,
                owner_id: str = "") -> str:
        """Record a consumer's need for an instrument (returns the lease id).

        Acquiring the same key twice creates a SECOND lease: the
        subscription is reference-counted, so one consumer releasing its
        lease never removes another consumer's instrument.

        The instrument may be given as a provider storage key, or as a
        CANONICAL id (the key is then resolved from the catalogue, and the
        provider is chosen by the routing rules).
        """
        key = str(key or "").strip()
        pinned = provider is not None
        if not key and canonical_id:
            offers = self._offers(canonical_id, "")
            if offers:
                from app.subscriptions.routing import select_offer
                chosen = select_offer(offers)
                if chosen is not None:
                    # Provisional key only: the provider is decided by the
                    # allocation (utilization), not by the catalogue order.
                    key = chosen.key
        if not key:
            raise ValueError("an instrument key or a canonical id is required")
        lease_id = uuid.uuid4().hex
        lease = Lease(lease_id=lease_id, key=key, provider=provider,
                      canonical_id=canonical_id, priority=int(priority),
                      owner_kind=owner_kind, owner_id=owner_id, pinned=pinned)
        with self._lock:
            self._leases[lease_id] = lease
            self._by_key.setdefault(key, []).append(lease_id)
        return lease_id

    def release(self, lease_id: str) -> bool:
        """Release one lease (the instrument stays while others hold it)."""
        with self._lock:
            lease = self._leases.pop(lease_id, None)
            if lease is None:
                return False
            holders = self._by_key.get(lease.key, [])
            if lease_id in holders:
                holders.remove(lease_id)
            if not holders:
                self._by_key.pop(lease.key, None)
            return True

    def release_owner(self, owner_kind: str, owner_id: str) -> int:
        """Release every lease of one owner (e.g. a deleted alert)."""
        with self._lock:
            doomed = [lid for lid, lease in self._leases.items()
                      if lease.owner_kind == owner_kind
                      and lease.owner_id == owner_id]
        for lid in doomed:
            self.release(lid)
        return len(doomed)

    def refcount(self, key: str) -> int:
        with self._lock:
            return len(self._by_key.get(str(key), []))

    # -- demand ----------------------------------------------------------------

    def _canonical_for_key(self, key: str) -> str | None:
        """The canonical id of a provider-native key (catalogue is authority).

        The demand arrives as provider-native keys, but ROUTING and
        FAILOVER need the cross-provider identity: two providers list the
        same contract under different keys, and only the canonical id says
        they are the same contract. The catalogue is the authority on which
        provider published which identifier, so the canonical id is read
        from the catalogue row that owns the key.
        """
        if not key or self._catalog is None:
            return None
        try:
            row = self._catalog.get_by_key(str(key))
        except Exception:
            row = None
        if not row:
            return None
        try:
            from app.canonical_identity import canonical_id_for_row
            return canonical_id_for_row(row)
        except Exception:
            return None

    def apply_demand(self, requests: list[dict[str, Any]]) -> dict[str, Any]:
        """Translate the resolved demand into leases (the lease model).

        ``requests`` are the demand records produced by the subscription
        service (``lease_requests()``): one record per concrete contract
        the user actually asked for, carrying the provider-native key, the
        canonical id, the owner and the priority.

        This is the ONLY way the demand enters the lease model, and it is
        a REPLACEMENT, not an accumulation: the leases this method created
        on the previous call are released first, so the lease set is
        always a function of the current demand - an instrument nobody
        asks for any more is unsubscribed, and one that is asked for
        twice is leased once per owner.

        Leases owned by anyone else (e.g. an alert's dependency lease) are
        never touched.

        Returns a summary: the number of requested contracts, how many
        leases were created/released, and the ones that could not be
        represented as a lease (with the reason) so a caller can report
        them instead of dropping them silently.
        """
        wanted: dict[tuple[str, str, str], tuple[dict[str, Any], str | None]] = {}
        rejected: list[dict[str, Any]] = []
        for record in requests or []:
            key = str(record.get("key") or "").strip()
            canonical_id = record.get("canonical_id") or self._canonical_for_key(key)
            owner_kind = str(record.get("owner_kind") or OWNER_OTHER)
            owner_id = str(record.get("owner_id") or owner_kind)
            if not key and not canonical_id:
                rejected.append({
                    "key": key,
                    "canonical_id": canonical_id,
                    "reason": "the demand record names no instrument",
                })
                continue
            identity = (owner_kind, owner_id, canonical_id or key)
            wanted[identity] = (record, canonical_id)

        released = 0
        with self._lock:
            stale = [identity for identity in self._demand_leases
                      if identity not in wanted]
        for identity in stale:
            lease_id = self._demand_leases.pop(identity, "")
            if lease_id and self.release(lease_id):
                released += 1

        created = 0
        for identity, (record, canonical_id) in wanted.items():
            if identity in self._demand_leases:
                continue
            key = str(record.get("key") or "").strip()
            owner_kind = identity[0]
            owner_id = identity[1]
            priority = int(record.get("priority") or PRIORITY_OTHER)
            try:
                lease_id = self.acquire(
                    key,
                    provider=record.get("provider") or None,
                    canonical_id=canonical_id,
                    priority=priority,
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                )
            except ValueError as exc:
                rejected.append({
                    "key": key,
                    "canonical_id": canonical_id,
                    "reason": str(exc),
                })
                continue
            self._demand_leases[identity] = lease_id
            created += 1

        return {
            "requested": len(requests or []),
            "leases": created,
            "released": released,
            "held": len(self._demand_leases),
            "rejected": rejected,
        }

    # -- routing / allocation ------------------------------------------------

    def _offers(self, canonical_id: str | None,
                key: str) -> list[Any]:
        """The providers that can serve a contract."""
        if not canonical_id:
            return []
        self._refresh_catalog_cache()
        cached = self._offers_cache.get(canonical_id)
        if cached is not None:
            return cached
        try:
            from app.subscriptions.routing import catalogue_offers
            offers = catalogue_offers(self._catalog, canonical_id)
        except Exception:
            offers = []
        if len(self._offers_cache) > 8192:
            self._offers_cache.clear()
        self._offers_cache[canonical_id] = list(offers)
        return list(offers)

    def _refresh_catalog_cache(self) -> None:
        """Drop the catalogue read models when the catalogue changed."""
        epoch = None
        try:
            epoch = self._catalog.catalog_epoch() if self._catalog else None
        except Exception:
            epoch = None
        if epoch != self._catalog_epoch:
            self._catalog_epoch = epoch
            self._offers_cache.clear()
            self._metadata_cache.clear()

    def _provider_available(self, provider: str) -> bool:
        """True when the provider's feed can accept subscriptions now."""
        status = None
        if self._status_provider is not None:
            try:
                status = self._status_provider(provider)
            except Exception:
                status = None
        if status is None:
            try:
                status = self.registry.status(provider)
            except Exception:
                status = None
        if not status:
            return True
        return str(status).strip().lower() not in _UNAVAILABLE_STATES

    def _budget(self, provider: str) -> tuple[int | None, str, str, bool]:
        """The provider's verified ceiling (and where it comes from)."""
        try:
            ent = self.registry.entitlement(provider)
        except Exception:
            return None, "", "", False
        if ent is None:
            return None, "", "", False
        return ent.symbols, ent.tier, ent.source, ent.is_verified

    def plan(self) -> dict[str, ProviderPlan]:
        """Allocate the demand across the providers.

        The leases are admitted in PRIORITY order (highest first), so a
        higher-priority demand takes the slot before a lower-priority one
        is admitted - the broker's limit is never exceeded, not even
        transiently, and nothing has to be evicted after the fact.

        Routing per contract:
          * only providers that LIST the contract can serve it;
          * unique coverage wins over the configuration order;
          * otherwise the provider with the most remaining verified
            capacity takes it (ties: configuration order);
          * a provider that is unavailable or out of capacity hands the
            contract to another enabled provider that lists it - that is
            the FAILOVER path.

        Whatever cannot be served is reported as ``capacity_limited`` (or
        ``provider_unavailable``) with the reason; it is never dropped
        silently.
        """
        plans: dict[str, ProviderPlan] = {}
        with self._lock:
            leases = list(self._leases.values())

        capacity_left: dict[str, int | None] = {}
        limits: dict[str, int | None] = {}
        for pid in self._registry_ids():
            budget, tier, source, verified = self._budget(pid)
            capacity_left[pid] = budget
            limits[pid] = budget
            plans[pid] = ProviderPlan(provider=pid, budget=budget, tier=tier,
                                      source=source, verified=verified)
        assigned: dict[str, int] = {}
        # The provider that currently serves each canonical contract (one
        # contract -> one provider at a time).
        contract_holders: dict[str, str] = {}
        try:
            preference = list(self.registry.preference_order())
        except Exception:
            preference = []

        # Priority order first: the highest-priority demand is served
        # first, so it never loses a slot to optional demand.
        # Deterministic order: priority first, then a STABLE business key
        # (never the random lease id), so the same demand always produces
        # the same allocation.
        ordered_leases = sorted(
            leases,
            key=lambda lease: (-lease.priority,
                               lease.canonical_id or "",
                               lease.key,
                               lease.owner_kind,
                               lease.owner_id))

        for lease in ordered_leases:
            offers = self._offers(lease.canonical_id, lease.key)
            # Only a provider that can STREAM the contract may hold the
            # subscription (a contract no provider can stream is reported,
            # never subscribed with a symbol the provider cannot use).
            streamable = [o for o in offers if o.streamable]
            provider = lease.provider
            chosen = None
            # A lease that was failed over to another provider goes BACK to
            # the provider the DEMAND asked for as soon as that provider is
            # available again and lists the contract. Without this the
            # allocation would keep the failover forever, even after the
            # original provider recovered.
            if (lease.origin_provider and lease.origin_provider != provider
                    and self._provider_available(lease.origin_provider)):
                home = next((o for o in streamable
                             if o.provider == lease.origin_provider), None)
                if home is not None:
                    self._move_lease(lease, home.provider, home.key)
                    provider = home.provider
                    lease.state = STATE_SUBSCRIBED
                    lease.reason = (f"returned to the demand's provider "
                                    f"{home.provider} (available again)")
            if not lease.pinned and streamable:
                from app.subscriptions.routing import choose_offer
                chosen = choose_offer(streamable, assigned=assigned,
                                      limits=limits, preference=preference)
                if chosen is not None:
                    provider = chosen.provider
            # ONE canonical contract goes to ONE provider at a time. A
            # second lease for the SAME contract (e.g. an alert lease on a
            # contract the demand already holds) is COALESCED onto the
            # provider that already serves it - a provider never receives
            # a second copy of a contract another provider serves.
            if lease.canonical_id:
                holder = contract_holders.get(lease.canonical_id)
                if holder is not None and holder != provider:
                    same = next((o for o in streamable
                                 if o.provider == holder), None)
                    if same is not None:
                        self._move_lease(lease, holder, same.key)
                        provider = holder
                        chosen = None
                        lease.state = STATE_SUBSCRIBED
                        lease.reason = (f"coalesced onto {holder}, which "
                                        "already serves this contract")
            if not provider:
                lease.state = STATE_UNROUTED
                lease.reason = ("no enabled provider lists this instrument")
                self._record_unrouted(plans, lease)
                continue

            # The lease's provider is known from here on: a failover must
            # be able to record WHERE THE DEMAND wanted the contract (the
            # origin), so the lease can go back when that provider is
            # available again.
            lease.provider = provider

            # The chosen provider must be able to serve it RIGHT NOW.
            if not self._provider_available(provider) or \
                    self._is_full(provider, capacity_left):
                replacement = self._failover_target(lease, streamable,
                                                   capacity_left)
                if replacement is not None:
                    self._move_lease(lease, replacement.provider,
                                     replacement.key)
                    provider = replacement.provider
                    chosen = None      # the key already follows the provider
                    lease.state = STATE_SUBSCRIBED
                    lease.reason = ("rerouted from the unavailable or full "
                                    f"provider to {provider}")
                else:
                    # WHY the contract could not be served decides the
                    # report: a provider that is available but FULL is a
                    # CAPACITY limit; a provider that cannot be reached (or
                    # a contract no other enabled provider lists) is an
                    # UNAVAILABILITY.
                    #
                    # An UNAVAILABLE provider is never reported as a
                    # capacity limit: the reason a contract could not be
                    # admitted must name the real fault.
                    provider_available = self._provider_available(provider)
                    full_alternative = any(
                        o.provider != provider
                        and self._provider_available(o.provider)
                        and self._is_full(o.provider, capacity_left)
                        for o in streamable)
                    if not provider_available:
                        lease.state = STATE_UNAVAILABLE
                        lease.reason = (
                            f"provider {provider} is unavailable and no other "
                            "enabled provider has capacity for this "
                            "instrument")
                    elif full_alternative:
                        lease.state = STATE_CAPACITY
                        lease.reason = (
                            "every enabled provider that lists this "
                            "instrument is at its verified ceiling "
                            f"({capacity_left.get(provider)} instruments "
                            "left) - the contract could not be admitted")
                    else:
                        lease.state = STATE_CAPACITY
                        lease.reason = (
                            f"{provider} is at its verified ceiling "
                            f"({capacity_left.get(provider)} instruments) and "
                            "no other enabled provider can take this "
                            "instrument")
                    self._record_limited(plans, lease, provider)
                    continue

            if chosen is not None and chosen.key != lease.key:
                # The key follows the provider the allocation chose.
                self._move_lease(lease, provider, chosen.key)
            lease.provider = provider
            lease.state = STATE_SUBSCRIBED
            lease.reason = ""
            plan = plans.setdefault(provider, ProviderPlan(provider=provider))
            if lease.key not in plan.admitted:
                # ONE contract is admitted ONCE per provider (two leases
                # for the same contract never duplicate the subscription).
                plan.admitted.append(lease.key)
            assigned[provider] = assigned.get(provider, 0) + 1
            if lease.canonical_id:
                contract_holders.setdefault(lease.canonical_id, provider)
            if capacity_left.get(provider) is not None:
                capacity_left[provider] = max(0, capacity_left[provider] - 1)

        return plans

    def _is_full(self, provider: str,
                 capacity_left: dict[str, int | None]) -> bool:
        left = capacity_left.get(provider)
        return left is not None and left <= 0

    def _move_lease(self, lease: Lease, provider: str, key: str) -> None:
        """Move a lease to another provider (the index follows the key)."""
        with self._lock:
            if lease.origin_provider is None:
                # Remember where the DEMAND wanted this contract, so the
                # lease can go back when that provider becomes available.
                lease.origin_provider = lease.provider
                lease.origin_key = lease.key
            holders = self._by_key.get(lease.key, [])
            if lease.lease_id in holders:
                holders.remove(lease.lease_id)
            if not holders:
                self._by_key.pop(lease.key, None)
            lease.key = key
            lease.provider = provider
            self._by_key.setdefault(key, []).append(lease.lease_id)

    def _record_unrouted(self, plans: dict[str, ProviderPlan],
                         lease: Lease) -> None:
        target = plans.setdefault("unrouted", ProviderPlan(provider="unrouted"))
        target.unreachable.append(lease.key)

    def _record_limited(self, plans: dict[str, ProviderPlan],
                        lease: Lease, provider: str | None = None) -> None:
        """Report a demand that could not be served (never dropped).

        A demand blocked by an UNREACHABLE provider is reported as
        ``provider_unavailable``; only a demand blocked by the provider's
        verified ceiling is reported as ``capacity_limited``. Reporting a
        broken provider as a full one hides the real fault.
        """
        owner = provider or lease.provider or "unrouted"
        lease.provider = lease.provider or owner
        target = plans.setdefault(owner, ProviderPlan(provider=owner))
        entry = {
            "key": lease.key,
            "canonical_id": lease.canonical_id,
            "owner_kind": lease.owner_kind,
            "owner_id": lease.owner_id,
            "state": lease.state,
            "reason": lease.reason,
        }
        if lease.state == STATE_UNAVAILABLE:
            target.provider_unavailable.append(entry)
        else:
            target.capacity_limited.append(entry)

    def _failover_target(self, lease: Lease, offers: list[Any],
                         capacity_left: dict[str, int | None] | None = None,
                         ) -> Any:
        """The failover target for a contract whose provider is down/full.

        The replacement must LIST the contract, be enabled, be available and
        still have capacity. When there is no such provider the lease stays
        with the failed provider and is reported - the failure is never
        hidden and no stale quote is presented as live.
        """
        offers = [o for o in offers
                  if o.streamable
                  and o.provider != lease.provider
                  and self._provider_available(o.provider)
                  and (capacity_left is None
                       or capacity_left.get(o.provider, 1) is None
                       or capacity_left.get(o.provider, 0) > 0)]
        if not offers:
            return None
        from app.subscriptions.routing import failover_offer
        if capacity_left is None:
            capacity_left = {pid: self._budget(pid)[0]
                             for pid in self._registry_ids()}
        return failover_offer(offers, lease.provider,
                              capacity_left=capacity_left)

    def _registry_ids(self) -> list[str]:
        try:
            return list(self.registry.ids())
        except Exception:
            return []

    # -- runtime state --------------------------------------------------------

    def note_data(self, key: str, when: str) -> None:
        """Record that DATA arrived for an instrument (the live evidence)."""
        with self._lock:
            self._last_data_at[str(key)] = str(when)

    def last_data_at(self, key: str) -> str | None:
        with self._lock:
            return self._last_data_at.get(str(key))

    def _feed_status(self, provider: str) -> dict[str, Any]:
        if self._feed_provider is None:
            return {}
        try:
            feed = self._feed_provider(provider)
        except Exception:
            return {}
        if feed is None:
            return {}
        try:
            return feed.status() or {}
        except Exception:
            return {}

    @staticmethod
    def _data_is_stale(seen: Any, canonical_id: str | None = None) -> bool:
        """True when the last data for an instrument is too old to be live.

        The window is the SHARED per-kind freshness policy, so the
        subscription projection and the quote read path never disagree
        about whether the same value is live.
        """
        if not seen:
            return True
        try:
            if isinstance(seen, datetime):
                moment = seen
            else:
                moment = datetime.fromisoformat(
                    str(seen).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
        except Exception:
            return True
        try:
            from app.quote_service import fresh_window_for_canonical
            window = fresh_window_for_canonical(canonical_id)
        except Exception:
            window = 120.0
        age = (datetime.now(timezone.utc) - moment).total_seconds()
        return age > window

    def instrument_states(self) -> list[dict[str, Any]]:
        """The runtime state of every instrument (with its reason)."""
        out: list[dict[str, Any]] = []
        with self._lock:
            leases = list(self._leases.values())
            last_data = dict(self._last_data_at)
        by_key: dict[str, list[Lease]] = {}
        for lease in leases:
            by_key.setdefault(lease.key, []).append(lease)
        for key, holders in by_key.items():
            provider = next((h.provider for h in holders if h.provider), None)
            status = self._feed_status(provider) if provider else {}
            state = holders[0].state
            reason = holders[0].reason
            if state not in (STATE_CAPACITY, STATE_UNROUTED):
                feed_state = str(status.get("state") or "")
                if feed_state and feed_state.lower() in _UNAVAILABLE_STATES:
                    state = STATE_UNAVAILABLE
                    reason = f"provider {provider} is {feed_state}"
                elif status.get("last_data_at", {}).get(key) or \
                        last_data.get(key):
                    seen = (status.get("last_data_at", {}).get(key)
                            or last_data.get(key))
                    canonical = next((h.canonical_id for h in holders
                                      if h.canonical_id), None)
                    if self._data_is_stale(seen, canonical):
                        # The instrument received data once, but not
                        # recently: the last known value is stale, NOT
                        # live.
                        state = STATE_STALE
                        reason = ("last data is older than the freshness "
                                  "window")
                    else:
                        state = STATE_LIVE
                        reason = ""
                elif status.get("state") == "streaming":
                    state = STATE_SUBSCRIBED
                    reason = "subscribed, no data received yet"
                else:
                    state = STATE_UNAVAILABLE
                    reason = (holders[0].reason
                              or f"provider {provider} is not streaming")
            out.append({
                "key": key,
                "provider": provider,
                "canonical_id": next((h.canonical_id for h in holders
                                      if h.canonical_id), None),
                "state": state,
                "reason": reason,
                "holders": len(holders),
                "last_data_at": status.get("last_data_at", {}).get(key)
                or last_data.get(key),
            })
        return out

    # -- reconciliation -------------------------------------------------------

    async def reconcile(self, feed_provider: Callable[[str], Any] | None = None
                        ) -> dict[str, Any]:
        """Reconcile the admitted leases with the live provider feeds.

        ``feed_provider`` overrides the manager's own feed lookup for this
        call (the caller knows which feeds it is applying to).

        A provider that cannot be reached does not abort the other
        provider's reconciliation.
        """
        async with self._reconcile_lock:
            return await self._reconcile(feed_provider)

    async def _reconcile(self, feed_provider: Callable[[str], Any] | None = None
                         ) -> dict[str, Any]:
        feeds = feed_provider or self._feed_provider
        plans = self.plan()
        results: dict[str, Any] = {}
        for provider, plan in plans.items():
            feed = None
            if feeds is not None:
                try:
                    feed = feeds(provider)
                except Exception:
                    feed = None
            if feed is None:
                results[provider] = {
                    "applied": False,
                    "reason": "feed unavailable",
                    "desired": len(plan.admitted),
                    "added": 0,
                    "removed": 0,
                    "provider_unavailable": len(plan.provider_unavailable),
                    "capacity_limited": len(plan.capacity_limited),
                }
                for lease in self._leases_for(provider):
                    if lease.state == STATE_SUBSCRIBED:
                        lease.state = STATE_UNAVAILABLE
                        lease.reason = f"provider {provider} feed unavailable"
                continue
            # The provider's availability is an EVENT, not a poll: chain the
            # manager into the feed's state listener so a provider that
            # becomes available (or drops) re-plans the existing demand.
            self._install_state_hook(provider, feed)
            current = set(getattr(feed, "_instrument_keys", ())
                          or getattr(feed, "_desired", ()))
            to_add = [k for k in plan.admitted if k not in current]
            to_remove = [k for k in current if k not in set(plan.admitted)]
            added = removed = 0
            mutations_before = self._mutation_failures(feed)
            try:
                if to_add:
                    added = await self._add_instruments(
                        feed, to_add, self._metadata_for_keys(to_add, provider))
                if to_remove:
                    keep = current - set(to_remove)
                    if keep:
                        removed = await feed.remove_instruments(to_remove) or 0
                    else:
                        to_remove = []
                mutations_after = self._mutation_failures(feed)
                if mutations_after > mutations_before:
                    # The feed refused to send a subscription mutation (the
                    # socket was closed or retired). The apply FAILED: it is
                    # never reported as a successful reconciliation.
                    reason = self._mutation_error(feed) or "mutation_not_sent"
                    results[provider] = {
                        "applied": False,
                        "reason": reason,
                        "detail": "subscription mutation was not sent",
                        "desired": len(plan.admitted),
                        "added": added,
                        "removed": removed,
                        "provider_unavailable": len(plan.provider_unavailable),
                        "capacity_limited": len(plan.capacity_limited),
                    }
                    logger.error(
                        "subscription manager: apply to %s FAILED "
                        "(%s: the subscription frame was not sent)",
                        provider, reason)
                    continue
                results[provider] = {
                    "applied": True,
                    "desired": len(plan.admitted),
                    "added": added,
                    "removed": removed,
                    "budget": plan.budget,
                    "capacity_limited": len(plan.capacity_limited),
                    "provider_unavailable": len(plan.provider_unavailable),
                }
            except Exception as exc:
                # A failed apply is NEVER reported as a successful
                # reconciliation: the failure is explicit and carries the
                # reason.
                results[provider] = {
                    "applied": False,
                    "reason": type(exc).__name__,
                    "detail": str(exc)[:200],
                    "desired": len(plan.admitted),
                    "added": added,
                    "removed": removed,
                    "provider_unavailable": len(plan.provider_unavailable),
                    "capacity_limited": len(plan.capacity_limited),
                }
                logger.error("subscription manager: apply to %s FAILED (%s: %s)",
                             provider, type(exc).__name__, exc)
        failed = sorted(pid for pid, res in results.items()
                        if not res.get("applied"))
        self._last_apply = {
            "at": asyncio.get_event_loop().time(),
            "plans": {pid: plan.describe() for pid, plan in plans.items()},
            "results": results,
        }
        if failed:
            logger.warning(
                "subscription manager: reconcile applied for %s; FAILED for %s",
                ", ".join(sorted(p for p in results if p not in failed)) or "-",
                ", ".join(failed),
            )
        elif any(res.get("added") or res.get("removed")
                 for res in results.values()):
            logger.info(
                "subscription manager: reconcile applied for %s",
                ", ".join(f"{pid}={results[pid].get('added', 0)} added"
                          for pid in sorted(results)),
            )
        else:
            # Nothing changed: the subscribed set already matches the
            # demand. Logged at debug level so a reconnect loop does not
            # flood the log with identical lines.
            logger.debug("subscription manager: reconcile applied (no change)")
        return {"plans": {pid: plan.describe() for pid, plan in plans.items()},
                "results": results}

    @staticmethod
    def _mutation_failures(feed: Any) -> int:
        """The feed's refused-mutation counter (0 when unsupported)."""
        try:
            status = feed.status() or {}
        except Exception:
            return 0
        try:
            return int(status.get("mutation_failures") or 0)
        except Exception:
            return 0

    @staticmethod
    def _mutation_error(feed: Any) -> str | None:
        """The reason the feed refused its last mutation (if any)."""
        try:
            status = feed.status() or {}
        except Exception:
            return None
        reason = status.get("last_mutation_error")
        return str(reason) if reason else None

    async def _add_instruments(self, feed: Any, keys: list[str],
                               metadata: dict[str, tuple[str, str]]) -> int:
        """Add keys to a feed, binding the identity metadata when supported.

        Without the metadata the feed cannot map a runtime-subscribed key to
        an instrument and drops every tick for it as unknown.
        """
        if not keys:
            return 0
        try:
            added = await feed.add_instruments(keys, metadata=metadata or None)
        except TypeError:
            added = await feed.add_instruments(keys)
        return int(added or 0)

    def _metadata_for_keys(self, keys: list[str],
                           provider: str) -> dict[str, tuple[str, str]]:
        """Bind {instrument_key: (exchange, tradingsymbol)} from the catalogue.

        The catalogue is the authority on which provider published which
        trading symbol; the feed needs the binding to normalize a tick into
        a quote for a runtime-subscribed key.
        """
        meta: dict[str, tuple[str, str]] = {}
        if self._catalog is None:
            return meta
        self._refresh_catalog_cache()
        for key in keys:
            cached = self._metadata_cache.get((provider, key))
            if cached is not None:
                meta[key] = cached
                continue
            row = None
            try:
                row = self._catalog.get(provider, key)
            except Exception:
                row = None
            if not row:
                try:
                    row = self._catalog.get_by_key(key)
                except Exception:
                    row = None
            if not row:
                continue
            try:
                symbol = str(row.get("tradingsymbol") or "").strip()
                exchange = str(row.get("exchange") or "").strip()
            except Exception:
                continue
            if not symbol:
                continue
            if not exchange and "|" in key:
                exchange = key.split("|", 1)[0].split("_", 1)[0]
            if exchange:
                binding = (exchange, symbol)
                meta[key] = binding
                if len(self._metadata_cache) > 8192:
                    self._metadata_cache.clear()
                self._metadata_cache[(provider, key)] = binding
        return meta

    # -- provider availability events ----------------------------------------

    def _install_state_hook(self, provider: str, feed: Any) -> None:
        """Chain the manager into the feed's state listener.

        The composition root owns the listener; the manager wraps it (and
        keeps the original) so a provider that becomes available, drops or
        reconnects re-plans the EXISTING demand. No second applier is
        created - the manager remains the only owner of the subscribed set.
        """
        if feed is None:
            return
        try:
            current = getattr(feed, "on_state_change", None)
        except Exception:
            return
        marker = id(feed)
        wrapper = self._state_hooks.get(marker)
        if wrapper is not None and current is wrapper:
            return

        def _hook(name: str, prov: str, old: str, new: str, reason: str | None,
                  _orig: Any = current, _provider: str = provider) -> None:
            try:
                if _orig is not None:
                    _orig(name, prov, old, new, reason)
            except Exception:  # pragma: no cover - never break the feed
                logger.debug("subscription manager: state listener raised",
                             exc_info=True)
            try:
                self.notify_provider_state(prov or _provider, new, reason)
            except Exception:  # pragma: no cover
                logger.debug("subscription manager: state notify failed",
                             exc_info=True)

        self._state_hooks[marker] = _hook
        try:
            feed.on_state_change = _hook
        except Exception:
            self._state_hooks.pop(marker, None)

    def notify_provider_state(self, provider: str, state: str,
                              reason: str | None = None) -> None:
        """A provider changed state: re-plan and re-apply the demand."""
        if not provider:
            return
        self._provider_events.append({
            "provider": provider,
            "state": state,
            "reason": reason,
            "at": time.time(),
        })
        del self._provider_events[:-50]
        if str(state or "").strip().lower() not in _SIGNIFICANT_STATES:
            # A transient transition: the availability did not change, so
            # the allocation would be the same. No reconcile, no storm.
            logger.debug("subscription manager: provider %s -> %s "
                         "(transient, no reconcile)", provider, state)
            return
        self._schedule_reconcile(provider, state, reason)

    def _schedule_reconcile(self, provider: str, state: str,
                            reason: str | None) -> None:
        """Schedule one reconciliation for a provider state change.

        A state change inside the debounce window is NOT dropped: a
        TRAILING reconcile is scheduled so the LAST state is always
        applied. Without it a disconnect/reconnect flap inside the window
        would leave the demand allocated to the failed-over provider for
        ever.
        """
        now = time.monotonic()
        last = self._last_reconcile_at.get(provider)
        if last is not None and (now - last) < self._reconcile_debounce_s:
            self._schedule_trailing_reconcile(
                provider, state, reason,
                last + self._reconcile_debounce_s)
            return
        self._last_reconcile_at[provider] = now
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.info("subscription manager: provider %s -> %s (no running "
                        "loop; reconcile deferred to the next demand change)",
                        provider, state)
            return
        try:
            task = loop.create_task(self._reconcile_after_change(provider,
                                                                 state, reason))
        except Exception:
            logger.debug("subscription manager: could not schedule reconcile",
                         exc_info=True)
            return
        self._reconcile_tasks.add(task)
        task.add_done_callback(self._reconcile_tasks.discard)

    def _schedule_trailing_reconcile(self, provider: str, state: str,
                                     reason: str | None,
                                     due_at: float) -> None:
        """Schedule the reconcile that applies the LAST provider state."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        previous = self._trailing_tasks.pop(provider, None)
        if previous is not None and not previous.done():
            previous.cancel()
        delay = max(0.0, due_at - time.monotonic())
        try:
            task = loop.create_task(
                self._trailing_reconcile(provider, state, reason, delay))
        except Exception:
            logger.debug("subscription manager: could not schedule the "
                         "trailing reconcile", exc_info=True)
            return
        self._trailing_tasks[provider] = task
        self._reconcile_tasks.add(task)
        task.add_done_callback(self._reconcile_tasks.discard)

    async def _trailing_reconcile(self, provider: str, state: str,
                                  reason: str | None, delay: float) -> None:
        """Apply the last provider state after the debounce window."""
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_reconcile_at[provider] = time.monotonic()
            await self._reconcile_after_change(provider, state, reason)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("subscription manager: trailing reconcile after "
                         "%s -> %s failed: %s", provider, state,
                         type(exc).__name__)

    async def _reconcile_after_change(self, provider: str, state: str,
                                      reason: str | None) -> None:
        """Re-plan the existing demand after a provider state change."""
        try:
            async with self._reconcile_lock:
                result = await self._reconcile()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("subscription manager: reconcile after %s -> %s "
                         "failed: %s", provider, state, type(exc).__name__)
            return
        changed = any(int(res.get("added") or 0)
                      or int(res.get("removed") or 0)
                      for res in (result.get("results") or {}).values())
        if changed:
            logger.info("subscription manager: re-planned demand after %s -> %s "
                        "(%s)", provider, state, reason or "no reason")
        else:
            logger.debug("subscription manager: demand unchanged after %s -> %s",
                         provider, state)

    def _leases_for(self, provider: str) -> list[Lease]:
        with self._lock:
            return [lease for lease in self._leases.values()
                    if lease.provider == provider]

    def status(self) -> dict[str, Any]:
        """A diagnostic projection (no secrets)."""
        plans = self.plan()
        return {
            "leases": len(self.leases()),
            "providers": {pid: plan.describe() for pid, plan in plans.items()},
            "instruments": self.instrument_states(),
            "last_apply": self._last_apply,
            "provider_events": list(self._provider_events[-10:]),
        }
