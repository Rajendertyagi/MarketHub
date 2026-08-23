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

__all__ = ["UpstoxError", "UpstoxAuthError", "UpstoxRestError", "UpstoxRateLimitError"]


class UpstoxError(Exception):
    """Base class for all Upstox adapter errors (safe messages only)."""


class UpstoxAuthError(UpstoxError):
    """Authentication/credential problem (invalid input, missing token,
    rejected credentials). Fatal until fresh valid credentials are
    supplied."""


class UpstoxRestError(UpstoxError):
    """REST call failure (D2.2).

    Attributes carry only SAFE parsed metadata:
        status_code   HTTP status, None for transport-level failures
        upstox_codes  provider error codes (e.g. UDAPI100050)
        retryable     whether the CALLER (D3 state machine) may retry

    Raw bodies, headers, URLs-with-query, tokens, codes and secrets are
    never stored — messages are built from parsed safe fields only.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        upstox_codes: tuple[str, ...] = (),
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.upstox_codes = tuple(upstox_codes)
        self.retryable = retryable


class UpstoxRateLimitError(UpstoxRestError):
    """HTTP 429 from Upstox (always retryable=True).

    ``retry_after_seconds`` is set only when the Retry-After header is a
    plain non-negative integer; timing decisions belong to D3.
    """

    def __init__(
        self,
        message: str,
        *,
        upstox_codes: tuple[str, ...] = (),
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(
            message, status_code=429, upstox_codes=upstox_codes, retryable=True
        )
        self.retry_after_seconds = retry_after_seconds
