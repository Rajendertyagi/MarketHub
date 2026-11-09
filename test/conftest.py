"""Pytest configuration for MarketHub test suite.

ISOLATION CONTRACT (mechanical, not advisory)
---------------------------------------------
The suite must never read or write the user's live ``data/events.db``
or live ``data/master.key``. Two mechanisms enforce that:

1. ``MARKETHUB_DATA_DIR`` is exported BEFORE any test module is imported
   and points the whole runtime (database, master key, logs) at a
   session-scoped temporary directory. Subprocesses started by the test
   helpers inherit the variable, so a spawned server is isolated too.
2. A guard wraps ``EventStore``/``CredentialStore`` construction: opening
   the PRODUCTION database path (or the production credential directory)
   raises ``RuntimeError`` instead of silently touching live data.

The live database and its existing rows are preserved: nothing here
deletes, resets or rewrites them.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# Add project root to path for imports
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

#: Session-scoped isolated runtime directory (set in pytest_configure).
_ISOLATED_DIR: Path | None = None

#: The live paths a test must never touch.
_PRODUCTION_DB = (_PROJECT_ROOT / "data" / "events.db").resolve()
_PRODUCTION_DATA_DIR = (_PROJECT_ROOT / "data").resolve()


def _install_production_data_guard() -> None:
    """Make any attempt to open the production DB fail loudly."""
    from core.persistence.store import EventStore

    original_init = EventStore.__init__
    if getattr(original_init, "_markethub_guarded", False):
        return

    def guarded_init(self, db_path: str) -> None:  # type: ignore[no-untyped-def]
        try:
            resolved = Path(str(db_path)).resolve()
        except OSError:  # pragma: no cover - defensive
            resolved = Path(str(db_path))
        if resolved == _PRODUCTION_DB:
            raise RuntimeError(
                "refusing to open the production database "
                f"({_PRODUCTION_DB}) from the test suite - "
                "tests must use an isolated data directory")
        return original_init(self, db_path)

    guarded_init._markethub_guarded = True  # type: ignore[attr-defined]
    EventStore.__init__ = guarded_init  # type: ignore[method-assign]

    from app.secrets_store import CredentialStore

    original_cred_init = CredentialStore.__init__
    if getattr(original_cred_init, "_markethub_guarded", False):
        return

    def guarded_cred_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        data_dir = kwargs.get("data_dir")
        if data_dir is None and len(args) >= 2:
            data_dir = args[1]
        if data_dir is not None:
            try:
                resolved = Path(str(data_dir)).resolve()
            except OSError:  # pragma: no cover - defensive
                resolved = Path(str(data_dir))
            if resolved == _PRODUCTION_DATA_DIR:
                raise RuntimeError(
                    "refusing to use the production credential directory "
                    f"({_PRODUCTION_DATA_DIR}) from the test suite")
        return original_cred_init(self, *args, **kwargs)

    guarded_cred_init._markethub_guarded = True  # type: ignore[attr-defined]
    CredentialStore.__init__ = guarded_cred_init  # type: ignore[method-assign]


def pytest_configure(config):
    """Register test markers and isolate the runtime data directory."""
    config.addinivalue_line("markers", "asyncio: mark test as async")
    config.addinivalue_line("markers", "slow: mark test as slow")

    global _ISOLATED_DIR
    if _ISOLATED_DIR is None:
        _ISOLATED_DIR = Path(tempfile.mkdtemp(prefix="markethub-test-"))
        os.environ["MARKETHUB_DATA_DIR"] = str(_ISOLATED_DIR)
    _install_production_data_guard()


def pytest_sessionfinish(session, exitstatus):
    """Remove the isolated runtime directory (never the production one)."""
    global _ISOLATED_DIR
    if _ISOLATED_DIR is not None:
        shutil.rmtree(_ISOLATED_DIR, ignore_errors=True)
        _ISOLATED_DIR = None
        os.environ.pop("MARKETHUB_DATA_DIR", None)


import pytest
from helpers.runner import R


@pytest.fixture(autouse=True)
def _reset_shared_request_budget():
    """Isolate the process-wide provider request budget between tests.

    The budget is shared BY DESIGN (one accounting state per provider);
    a test that trips a 429 must not block the next test.
    """
    from providers.rate import get_budget_manager

    get_budget_manager().reset()
    yield
    get_budget_manager().reset()


@pytest.fixture
def runner():
    """Shared test runner instance.

    The ``R`` helper records failures instead of raising, so a legacy
    "runner-style" test could report green under pytest while its
    assertions had failed. The fixture therefore fails the test when any
    recorded assertion failed - a recorded failure is always a real
    failure (the runner is also used to report expected exceptions).
    """
    r = R()
    yield r
    if r.failed:
        detail = "; ".join(r.failures[:5])
        raise AssertionError(
            f"{r.failed} assertion(s) failed: {detail}")


@pytest.fixture
def r():
    """Alias of :func:`runner` (some legacy files name the fixture ``r``)."""
    r_obj = R()
    yield r_obj
    if r_obj.failed:
        detail = "; ".join(r_obj.failures[:5])
        raise AssertionError(
            f"{r_obj.failed} assertion(s) failed: {detail}")


@pytest.fixture(scope="module")
def mcp_server():
    """Start a real subprocess MCP server for the whole module.

    Module-scoped so the 12 standalone-style tests (test_sdk_alignment.py,
    test_multi_client.py, test_sse_stream.py) that were written to run via
    ``main()`` get a live server under pytest. Teardown reuses the existing
    lifecycle ownership (stop subprocess, restore config, clean data_test and
    .test_logs, release port, clear helper state).
    """
    import asyncio

    from helpers.lifecycle import restore_environment, start_server

    asyncio.run(start_server())
    try:
        yield
    finally:
        restore_environment()
