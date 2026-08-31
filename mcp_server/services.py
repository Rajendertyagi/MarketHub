"""
Application services container for dependency passing.

Holds references to process-wide services that tools and resources need.
No request-scoped state lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Services:
    """Process-wide service dependencies for tool/resource registration."""

    store: Any           # EventStore
    subscription_bus: Any  # InMemorySubscriptionBus
    bg_task_manager: Any  # BackgroundTaskManager
    source_manager: Any   # SourceManager
    timeouts: dict[str, Any]
    replay_cfg: dict[str, Any]
    metrics: Any
    market_service: Any = None          # RuntimeMetrics
    alert_engine: Any = None            # market alert engine (WebUI parity)
    condition_alert_engine: Any = None  # advanced market_condition engine (B2/B4)
    condition_identity_resolver: Any = None  # provider-neutral identity resolver (B2)
    market_intel: Any = None            # unified search/discovery/chain
    instrument_catalog: Any = None      # canonical instrument catalog
    provider_market_data: Any = None    # history/option-chain services

