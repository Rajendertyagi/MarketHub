#!/usr/bin/env python3
"""Data-path isolation proof.

The suite must never touch the user's live ``data/events.db`` / master key.
These tests prove that mechanically:

  * the effective runtime directory is the session's isolated temp dir;
  * the DB and the master key land in that isolated dir;
  * a guard makes opening the PRODUCTION database (or credential dir)
    raise instead of silently mutating live data;
  * a spawned subprocess inherits the isolated runtime;
  * the production database file is byte-for-byte untouched by a test run.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

_PRODUCTION_DB = _PROJECT_DIR / "data" / "events.db"
_PRODUCTION_DIR = _PROJECT_DIR / "data"


def _stat(path: Path):
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def test_runtime_dir_is_isolated_from_production() -> None:
    from app.paths import DATA_ROOT
    from app.runtime_paths import runtime_data_dir, runtime_db_path

    effective = runtime_data_dir()
    assert effective != Path(DATA_ROOT).resolve(), (
        "the test session must not resolve to the production data dir")
    assert Path(runtime_db_path()).parent == effective
    assert Path(runtime_db_path()).name == "events.db"


def test_env_override_is_active_for_the_session() -> None:
    from app.runtime_paths import RUNTIME_DATA_DIR_ENV

    assert os.environ.get(RUNTIME_DATA_DIR_ENV), (
        "the session must export the runtime data-dir override")
    assert _PRODUCTION_DIR.resolve() not in Path(
        os.environ[RUNTIME_DATA_DIR_ENV]).resolve().parents


def test_production_database_open_is_refused() -> None:
    from core.persistence.store import EventStore

    try:
        EventStore(str(_PRODUCTION_DB))
    except RuntimeError as exc:
        assert "production database" in str(exc)
    else:  # pragma: no cover - the guard must fire
        raise AssertionError(
            "opening the production database must be refused in tests")


def test_production_credential_dir_is_refused() -> None:
    from app.secrets_store import CredentialStore
    from core.persistence.store import EventStore

    store = EventStore(str(Path(tempfile.gettempdir()) / "mk-iso-probe.db"))
    try:
        CredentialStore(store, data_dir=_PRODUCTION_DIR)
    except RuntimeError as exc:
        assert "production credential directory" in str(exc)
    else:  # pragma: no cover - the guard must fire
        raise AssertionError(
            "using the production credential dir must be refused in tests")


def test_isolated_store_does_not_touch_production_file() -> None:
    """Writing through the isolated store leaves the live file untouched."""
    from core.persistence.store import EventStore

    before = _stat(_PRODUCTION_DB)

    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(str(Path(tmp) / "events.db"))
        store.set_source_state("isolation_probe", "key", "value")
        rows = store.list_md_subscriptions()
        assert isinstance(rows, list)

    after = _stat(_PRODUCTION_DB)
    assert before == after, "the production database changed during a test"


def test_subprocess_inherits_the_isolated_runtime() -> None:
    """A spawned process (e.g. the MCP server) resolves the same dir."""
    from app.runtime_paths import runtime_data_dir

    code = (
        "import sys;"
        f"sys.path.insert(0, {str(_PROJECT_DIR)!r});"
        "from app.runtime_paths import runtime_data_dir;"
        "print(runtime_data_dir())"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == str(runtime_data_dir())


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
