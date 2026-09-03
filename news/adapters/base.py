"""News adapter protocol — the contract every source adapter must satisfy."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from market.models import NewsSourceConfig


@runtime_checkable
class NewsAdapter(Protocol):
    """Generic news adapter interface.

    Implementations fetch articles from a remote source (RSS, Reddit, etc.)
    and return canonical model instances.  The service layer owns dedup,
    filtering, and sentiment — adapters only fetch and normalize.
    """

    @property
    def source_type(self) -> str:
        """Matcher string that maps to NewsSourceConfig.source_type."""
        ...

    async def fetch(
        self,
        source: NewsSourceConfig,
        *,
        since: Any | None = None,
        limit: int = 50,
    ) -> list[Any]:
        """Fetch articles from *source*.

        Parameters
        ----------
        source:
            The persisted source configuration.
        since:
            Optional adapter-specific cursor (e.g. newest seen timestamp
            or Reddit fullname).  Adapters that don't support incremental
            fetch may ignore it.
        limit:
            Hard upper bound on items returned per call.

        Returns
        -------
        A list of canonical model instances (RSSEntry or RedditPost).
        """
        ...
