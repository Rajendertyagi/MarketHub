#!/usr/bin/env python3
"""Runtime data-directory resolution tests.

Contract:
  * the CANONICAL root (``app.paths.DATA_ROOT`` = PROJECT_ROOT/"data") is
    the effective runtime data directory for the application;
  * a ``config.json`` ``data_dir`` that points elsewhere is REPORTED as
    overridden, never silently honoured (no mixed paths);
  * an EXPLICIT per-run override (``MARKETHUB_DATA_DIR``) wins over both
    and is never redirected to the canonical root - this is what keeps the
    test suite away from the live database.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


def _without_env(monkeypatch) -> None:
    monkeypatch.delenv("MARKETHUB_DATA_DIR", raising=False)


def test_canonical_root_is_the_effective_runtime_dir(monkeypatch) -> None:
    from app.paths import DATA_ROOT
    from app.runtime_paths import runtime_data_dir
    _without_env(monkeypatch)
    assert runtime_data_dir(None) == Path(DATA_ROOT).resolve()
    assert runtime_data_dir("data") == Path(DATA_ROOT).resolve()


def test_diverging_configured_dir_is_overridden_not_honoured(monkeypatch) -> None:
    from app.paths import DATA_ROOT
    from app.runtime_paths import resolve_runtime_data_dir
    _without_env(monkeypatch)
    effective, source = resolve_runtime_data_dir("data_test")
    assert effective == Path(DATA_ROOT).resolve()
    assert source == "overridden"


def test_matching_configured_dir_is_canonical(monkeypatch) -> None:
    from app.paths import DATA_ROOT
    from app.runtime_paths import resolve_runtime_data_dir
    _without_env(monkeypatch)
    effective, source = resolve_runtime_data_dir("data")
    assert effective == Path(DATA_ROOT).resolve()
    assert source == "canonical"


def test_explicit_override_wins_over_canonical(monkeypatch) -> None:
    from app.paths import DATA_ROOT
    from app.runtime_paths import resolve_runtime_data_dir
    _without_env(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        effective, source = resolve_runtime_data_dir(None, override=tmp)
        assert effective == Path(tmp).resolve()
        assert source == "override"
        assert effective != Path(DATA_ROOT).resolve()
        assert (effective / "events.db").parent == effective


def test_env_override_is_honoured(monkeypatch) -> None:
    from app.paths import DATA_ROOT
    from app.runtime_paths import resolve_runtime_data_dir, runtime_db_path
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("MARKETHUB_DATA_DIR", tmp)
        effective, source = resolve_runtime_data_dir("data_test")
        assert effective == Path(tmp).resolve()
        assert source == "override"
        assert Path(runtime_db_path("data_test")) == Path(tmp).resolve() / \
            "events.db"
        # The canonical root is NOT used while an override is active.
        assert effective != Path(DATA_ROOT).resolve()


def test_db_and_master_key_resolve_to_the_same_dir(monkeypatch) -> None:
    from app.paths import DATA_ROOT
    from app.runtime_paths import describe_runtime, runtime_db_path
    _without_env(monkeypatch)
    d = describe_runtime("data_test")
    base = Path(DATA_ROOT).resolve()
    assert Path(d["db_path"]) == base / "events.db"
    assert Path(d["master_key_path"]) == base / "master.key"
    assert Path(runtime_db_path("data_test")) == base / "events.db"
    # No mixed paths: db, key and dir share one parent.
    assert Path(d["db_path"]).parent == Path(d["master_key_path"]).parent
    assert Path(d["db_path"]).parent == Path(d["data_dir"])


def test_isolated_override_is_not_redirected() -> None:
    from app.runtime_paths import is_isolated_override
    with tempfile.TemporaryDirectory() as tmp:
        assert is_isolated_override(tmp) is True
    assert is_isolated_override(None) is False
    assert is_isolated_override("data") is False


def test_server_uses_the_canonical_runtime_dir() -> None:
    """The composed app must resolve its store through the resolver."""
    from app.paths import DATA_ROOT
    src = (Path(__file__).resolve().parent.parent / "app" / "server.py")
    text = src.read_text(encoding="utf-8")
    # The DB path and the credential store must come from the resolver,
    # not from config.json's data_dir.
    assert "runtime_db_path(" in text, "server must use runtime_db_path()"
    assert "runtime_data_dir(" in text, "server must use runtime_data_dir()"
    assert "PROJECT_ROOT / DATA_DIR" not in text, (
        "server must not join PROJECT_ROOT with the configured data_dir")
    assert Path(DATA_ROOT).name == "data"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
