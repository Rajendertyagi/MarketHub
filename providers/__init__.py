"""Broker-neutral provider vocabulary + registry.

Provider capability/capacity metadata is declared by each broker adapter and
aggregated by :mod:`providers.registry`. Nothing here imports broker code.
"""

from providers.capabilities import ProviderCapabilities
from providers.registry import ProviderRegistry, get_registry, set_registry

__all__ = [
    "ProviderCapabilities",
    "ProviderRegistry",
    "get_registry",
    "set_registry",
]
