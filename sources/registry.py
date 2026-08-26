"""
Static source-type registry.

This module maps configuration ``"type"`` strings to concrete source
constructors. It uses explicit imports and a static dictionary — NOT dynamic
import scanning, entry points, or plugin directories — so the project remains
compatible with Nuitka onefile/standalone compilation.

To add a new built-in source:
    1. implement the source class or factory (e.g. sources/my_source.py)
    2. import it here and add an entry to SOURCE_TYPES
    3. add a config entry with "type": "<key>"

Sources whose constructor accepts a ``market_service`` keyword argument
automatically receive the shared application instance from
``build_source_manager``; sources that don't are constructed with config only.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Type

logger = logging.getLogger(__name__)

from sources.http_poller import HttpJsonPoller
from sources.test_source import TestSource


def _create_upstox_feed(config: dict, *, market_service: Any = None) -> Any:
    """Construct an UpstoxFeed from pure config data.

    Resolves ``$ENV_VAR`` access-token references using the project's
    existing convention, validates canonical instrument metadata, and
    constructs UpstoxCredentials + UpstoxRest + UpstoxFeed.
    ``market_service`` is injected by build_source_manager.
    """
    from brokers.upstox.auth import UpstoxCredentials
    from brokers.upstox.feed import UpstoxFeed
    from brokers.upstox.rest import UpstoxRest

    # -- resolve access token ($ENV_VAR convention) --------------------------
    token_ref = config.get("access_token", "")
    if isinstance(token_ref, str) and token_ref.startswith("$"):
        env_name = token_ref[1:]
        token = os.environ.get(env_name, "")
        if not token.strip():
            raise ValueError(
                f"upstox access token not resolved: environment variable "
                f"{env_name!r} is not set"
            )
    else:
        token = str(token_ref)
    if not token.strip():
        # OAuth-configured deployments have no static token:
        # construct with the known placeholder so the source
        # registers and gates on readiness until WebUI login
        # supplies real credentials.
        token = 'PENDING-OAUTH-LOGIN'
        logger.info(
            'upstox access_token not configured - source will gate on OAuth login')


    credentials = UpstoxCredentials(access_token=token)
    rest = UpstoxRest()

    # -- validate instruments -------------------------------------------------
    instruments_cfg = config.get("instruments", [])
    if not isinstance(instruments_cfg, list) or not instruments_cfg:
        raise ValueError(
            "upstox feed requires a non-empty 'instruments' list"
        )

    keys: list[str] = []
    metadata: dict[str, tuple[str, str]] = {}
    for i, instr in enumerate(instruments_cfg):
        if not isinstance(instr, dict):
            raise ValueError(
                f"instruments[{i}] must be an object with "
                f"key/exchange/tradingsymbol"
            )
        key = instr.get("key", "")
        exchange = instr.get("exchange", "")
        tradingsymbol = instr.get("tradingsymbol", "")
        if not isinstance(key, str) or not key.strip():
            raise ValueError(
                f"instruments[{i}].key must be a non-empty string"
            )
        if not isinstance(exchange, str) or not exchange.strip():
            raise ValueError(
                f"instruments[{i}].exchange must be a non-empty string"
            )
        if not isinstance(tradingsymbol, str) or not tradingsymbol.strip():
            raise ValueError(
                f"instruments[{i}].tradingsymbol must be a non-empty string"
            )
        keys.append(key.strip())
        metadata[key.strip()] = (exchange.strip(), tradingsymbol.strip())

    market_service = market_service

    return UpstoxFeed(
        config={
            "source_name": config.get("source_name", "upstox"),
            "mode": config.get("mode", "full"),
            "instrument_keys": keys,
        },
        credentials=credentials,
        rest=rest,
        market_service=market_service,
        instrument_metadata=metadata,
    )


# type key -> source class or factory callable


def _create_fyers_feed(config: dict, *, market_service: Any = None) -> Any:
    """Construct a FyersFeed from pure config data.

    The source config describes the SOURCE ONLY (type, mode, instruments).
    Secrets come exclusively from the encrypted credential store, which the
    composition root injects (``credential_store`` + ``access_token_getter``
    + ``redirect_uri``) — never from config.json. The feed resolves the App
    ID from the store at connect time, so credentials added after startup
    (first-run WebUI setup) are picked up without rebuilding the feed.
    """
    from brokers.fyers.auth import FyersAuth
    from brokers.fyers.feed import FyersFeed

    getter = config.get("access_token_getter")
    if not callable(getter):
        raise ValueError(
            "fyers feed requires an access_token_getter callable "
            "(wired by the composition root)")

    app_id = config.get("app_id")
    app_secret = config.get("app_secret")
    redirect_uri = config.get(
        "redirect_uri", "http://localhost:7070/auth/fyers/callback")

    # Build the FyersAuth only when credentials are already available. The
    # feed also resolves App ID from the store at connect, so a missing
    # app_id here is NOT fatal — the source simply isn't ready until the
    # operator configures credentials via Settings.
    auth = None
    if (isinstance(app_id, str) and app_id.strip()
            and isinstance(app_secret, str) and app_secret.strip()):
        auth = FyersAuth(app_id=app_id.strip(),
                         secret_id=app_secret.strip(),
                         redirect_uri=redirect_uri)

    keys = [i["key"] for i in config.get("instruments", [])
            if isinstance(i, dict) and i.get("key")]
    cfg = dict(config)
    cfg["instrument_keys"] = keys or ["NSE:NIFTY50-INDEX"]
    return FyersFeed(config=cfg, auth=auth, market_service=market_service)


SOURCE_TYPES: dict[str, Callable[..., Any]] = {
    "http_poller": HttpJsonPoller,
    "test_source": TestSource,
    "upstox_feed": _create_upstox_feed,
    "fyers_feed": _create_fyers_feed,
}

# Re-export for convenience / typing clarity.
SourceFactory = Callable[[dict[str, Any]], Any]

