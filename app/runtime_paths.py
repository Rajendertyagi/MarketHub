"""Resolve the canonical runtime data directory (single source of truth).

``app.paths.DATA_ROOT`` is the canonical anchor (``PROJECT_ROOT / "data"``).
The application must use it EXCLUSIVELY for persistent application data
(database, master key, credential store, logs) so no runtime path can
split across two directories.

``config.json`` may still carry a ``data_dir`` (it is preserved
byte-for-byte), but it must not silently relocate runtime state. This
module resolves the effective runtime directory ONCE, applies the
canonical root, and reports the source so the resolution is auditable.

**Explicit per-run override.** ``MARKETHUB_DATA_DIR`` points the whole
runtime at one directory (database, master key, logs) for the lifetime of
the process. It exists so an ISOLATED run - the test suite, a diagnostic
tooling run - can never touch the user's live ``data/events.db``. The
override is explicit, deliberate and always reported (``source ==
"override"``); it is never derived from, and never falls back to, the
canonical root.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.paths import DATA_ROOT, PROJECT_ROOT

#: Environment variable that redirects the runtime data directory.
RUNTIME_DATA_DIR_ENV = "MARKETHUB_DATA_DIR"

#: Reported source for an explicit (environment) override.
SOURCE_OVERRIDE = "override"
#: Reported source for the canonical root.
SOURCE_CANONICAL = "canonical"
#: Reported source when a configured ``data_dir`` was ignored.
SOURCE_OVERRIDDEN = "overridden"


def _env_override() -> str | None:
    """The explicit per-run override, if any."""
    raw = os.environ.get(RUNTIME_DATA_DIR_ENV, "")
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _absolute(path: str | os.PathLike[str]) -> Path:
    """Resolve ``path`` against the project root when it is relative."""
    candidate = Path(str(path))
    if not candidate.is_absolute():
        candidate = Path(PROJECT_ROOT) / candidate
    try:
        return candidate.resolve()
    except OSError:  # pragma: no cover - unresolvable path
        return candidate


def resolve_runtime_data_dir(
    configured: str | None = None,
    *,
    override: str | os.PathLike[str] | None = None,
    root: Path | None = None,
) -> tuple[Path, str]:
    """Return ``(effective_dir, source)`` for the runtime data directory.

    Resolution order:
      1. an EXPLICIT override (argument, else the ``MARKETHUB_DATA_DIR``
         environment variable) is used as-is - this is how an isolated
         run (tests, tooling) keeps itself away from the live database;
      2. otherwise the CANONICAL ``DATA_ROOT`` is used and a configured
         value that points elsewhere is REPORTED as ``overridden`` so the
         divergence is visible rather than silent.

    A caller that injects a directory explicitly (isolated tests) is
    never redirected to the canonical root.
    """
    base = Path(root) if root is not None else Path(DATA_ROOT)
    try:
        base = base.resolve() if base.exists() else base
    except OSError:  # pragma: no cover - unresolvable path
        pass

    explicit = override if override is not None else _env_override()
    if explicit is not None:
        return _absolute(explicit), SOURCE_OVERRIDE

    if not configured:
        return base, SOURCE_CANONICAL

    candidate = _absolute(configured)
    try:
        same = candidate == base
    except OSError:  # pragma: no cover - defensive
        same = str(candidate) == str(base)

    if same:
        return base, SOURCE_CANONICAL
    return base, SOURCE_OVERRIDDEN


def runtime_data_dir(configured: str | None = None) -> Path:
    """The effective runtime data directory."""
    return resolve_runtime_data_dir(configured)[0]


def runtime_db_path(configured: str | None = None) -> Path:
    """The effective runtime ``events.db`` path."""
    return runtime_data_dir(configured) / "events.db"


def describe_runtime(configured: str | None = None) -> dict[str, str]:
    """Human-readable, secret-free description of the runtime paths."""
    effective, source = resolve_runtime_data_dir(configured)
    return {
        "data_dir": str(effective),
        "data_dir_source": source,
        "db_path": str(effective / "events.db"),
        "master_key_path": str(effective / "master.key"),
        "configured_data_dir": str(configured) if configured else "",
        "override_env": RUNTIME_DATA_DIR_ENV,
    }


def is_isolated_override(path: str | os.PathLike[str] | None) -> bool:
    """True when ``path`` is an explicit per-run override (isolated test).

    An isolated override is any path that is NOT the canonical root and
    is not inside it - e.g. a pytest ``tmp_path`` or the directory named
    by ``MARKETHUB_DATA_DIR``. Such a path must never be redirected to
    the canonical root.
    """
    if path is None:
        return False
    candidate = Path(str(path))
    try:
        candidate = candidate.resolve()
    except OSError:
        pass
    base = Path(DATA_ROOT)
    try:
        base = base.resolve()
    except OSError:
        pass
    return candidate != base and base not in candidate.parents
