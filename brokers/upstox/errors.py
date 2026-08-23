"""
Upstox adapter error types (Phase D2.1).

Policy: exception messages contain SAFE, parsed information only.

    * no raw HTTP response bodies
    * no Authorization headers
    * no access tokens, API secrets, authorization codes, or signed URLs

D2.2 (REST client) builds these from parsed error fields; arbitrary
HTML/text/malformed bodies must become generic safe messages before an
exception is ever constructed. Keep the hierarchy small — add types only
when a concrete consumer needs to distinguish them.
"""

from __future__ import annotations

__all__ = ["UpstoxError", "UpstoxAuthError"]


class UpstoxError(Exception):
    """Base class for all Upstox adapter errors (safe messages only)."""


class UpstoxAuthError(UpstoxError):
    """Authentication/credential problem (invalid input, missing token,
    rejected credentials). Fatal until fresh valid credentials are
    supplied."""
