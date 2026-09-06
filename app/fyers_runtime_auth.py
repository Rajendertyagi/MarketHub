"""Single-owner runtime access-token state for the Fyers broker adapter.

This module is RUNTIME auth state only: the currently-active Fyers access
token used by the running FyersFeed through its injected ``access_token_getter``.
It is intentionally separate from:

* the durable/encrypted credential store (``app.secrets_store``) — encrypted
  access token, refresh token and PIN live there, never here; and
* the feed lifecycle (``sources.registry`` / ``SourceManager``) — start/stop/
  reconnect task ownership is not the concern of this object.

Both the startup/session-restore path (``app.server``) and the login/clear path
(``api.product_routes``) must mutate this owner through its methods. External
modules never touch the backing field directly, so there is exactly ONE
mutation owner for Fyers runtime auth state.

Concurrency: the backing field is a ``str``. In CPython, attribute assignment
of an object reference (here, a ``str``) is atomic, and ``get_access_token`` is
a single read of that reference. There is no read-modify-write on the field
itself, so plain reference assignment is sufficient — no lock is added
speculatively.
"""

from __future__ import annotations


class FyersRuntimeAuth:
    """Owns the single runtime Fyers access token (in-memory only)."""

    def __init__(self) -> None:
        self._access_token = ""

    def set_access_token(self, token: str) -> None:
        """Install a freshly-obtained access token (runtime memory only)."""
        self._access_token = token or ""

    def clear_access_token(self) -> None:
        """Drop the token (e.g. on logout / forget-session / restore failure)."""
        self._access_token = ""

    def get_access_token(self) -> str:
        """Read-only accessor used by the FyersFeed ``access_token_getter``."""
        return self._access_token

    def has_access_token(self) -> bool:
        """Read-only predicate used by status and startup gating."""
        return bool(self._access_token)
