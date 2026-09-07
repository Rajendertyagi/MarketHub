"""Market-data subscription foundation.

Single owner of the preference → resolution → runtime desired-set chain:

    DB preferences (core.persistence EventStore v17 tables)
        ↓  SubscriptionService.resolve()
    catalog resolution (canonical registry + instruments catalog)
        ↓
    concrete provider instrument keys (per provider)
        ↓  SubscriptionService.reconcile()
    live feed desired sets (feed.add_instruments / remove_instruments)

Canonical instrument definitions stay in CODE (app.market_indices
MAJOR_INDICES registry + the instruments catalog). The DB stores only
user preferences. config.json is no longer a runtime subscription source
(legacy ``sources.<feed>.instruments`` entries are imported once by
:meth:`SubscriptionService.migrate_from_config` and then deprecated).

No secrets, no provider payloads, no trading state live here.
"""

from app.subscriptions.service import SubscriptionService

__all__ = ["SubscriptionService"]
