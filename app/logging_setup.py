"""Centralized logging configuration for MarketHub.

Single owner of logging handler setup. Initialized ONCE at application
startup (app.server) — no other module may attach handlers ad hoc.

Design (locked):
    * Console output is preserved (stdout, human format).
    * A rotating file handler persists everything to
      ``<project_root>/data/logs/markethub.log`` so an incident's final
      event survives the console window closing.
      stdlib RotatingFileHandler: maxBytes=10 MiB, backupCount=5, utf-8.
    * Logging must NEVER prevent startup: directory creation or handler
      failures degrade to console-only with a stderr note.
    * The startup diagnostic header carries version / python / platform /
      source names / log path — NEVER credentials or config secret values.

Secret discipline: handlers change NOTHING about what reaches them. Callers
must keep using the project's existing redacted/safe representations
(e.g. brokers.upstox.feed._safe_ws_summary, typed REST error wording).
Tokens, client secrets, authorization codes, and authorized WSS URIs are
never logged by any lifecycle/auth path in this project.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import sys
from pathlib import Path
from typing import Iterable

__all__ = ["setup_logging", "log_startup_diagnostics"]

DEFAULT_LOG_RELATIVE = os.path.join("data", "logs")
LOG_FILE_NAME = "markethub.log"


class _BenignSocketClosureFilter(logging.Filter):
    """Drop 'Task exception was never retrieved' noise for NORMAL socket
    closures.

    When the feed closes the websocket while the websockets library has an
    internal recv() pending, that task raises ConnectionClosedOK
    (1000/1000) which nothing retrieves — asyncio logs it as ERROR. It is
    a normal, expected closure path, not a fault. Anything else (real
    errors, abnormal closures) still passes through.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.name != "asyncio":
                return True
            msg = record.getMessage()
            if "Task exception was never retrieved" not in msg:
                return True
            return "ConnectionClosedOK" not in msg
        except Exception:
            return True
MAX_BYTES = 10 * 1024 * 1024   # 10 MiB per file
BACKUP_COUNT = 5               # markethub.log.1 .. markethub.log.5
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_configured = False
_log_file_path: str | None = None


def setup_logging(
    project_root: str | Path | None = None,
    *,
    console: bool = True,
    force: bool = False,
) -> str | None:
    """Configure root logging once; return the active log file path.

    Args:
        project_root: repository root; defaults to this file's parent's parent.
        console: attach a stdout StreamHandler (kept for parity with the
            historical console behaviour).
        force: reconfigure even if already set up (test seam only).

    Returns:
        The log file path when file logging is active, else None
        (file logging unavailable — console-only degradation).

    Never raises: a broken log destination must not take MarketHub down.
    """
    global _configured, _log_file_path

    if _configured and not force:
        return _log_file_path

    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    formatter = logging.Formatter(_FORMAT)
    root.addFilter(_BenignSocketClosureFilter())

    # Detach handlers from previous configuration (force/idempotency).
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    if console:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    file_path: str | None = None
    try:
        log_dir = Path(project_root) / DEFAULT_LOG_RELATIVE
        log_dir.mkdir(parents=True, exist_ok=True)
        candidate = log_dir / LOG_FILE_NAME
        file_handler = logging.handlers.RotatingFileHandler(
            candidate,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        file_path = str(candidate)
    except Exception as exc:
        # Degrade to console-only; never block startup on logging problems.
        print(
            f"WARNING: file logging unavailable ({type(exc).__name__}); "
            f"continuing console-only",
            file=sys.stderr,
        )

    _configured = True
    _log_file_path = file_path
    return file_path


def log_startup_diagnostics(
    logger: logging.Logger,
    *,
    version: str | None = None,
    source_names: Iterable[str] = (),
    listen_host: str | None = None,
    listen_port: int | str | None = None,
    log_file: str | None = None,
) -> None:
    """Emit the one-time startup header. Safe values only — no secrets,
    no config.json contents beyond structural names."""
    try:
        commit = _best_effort_commit()
    except Exception:
        commit = None
    logger.info("=" * 60)
    logger.info("MarketHub %s starting%s",
                version or "(unknown)",
                f" (commit {commit})" if commit else "")
    logger.info("python %s on %s",
                platform.python_version(), platform.platform())
    names = ", ".join(sorted(source_names)) if source_names else "(none)"
    logger.info("sources: %s", names)
    if listen_host is not None and listen_port is not None:
        logger.info("listening on %s:%s", listen_host, listen_port)
    logger.info("log file: %s", log_file or "(console only)")
    logger.info("=" * 60)


def _best_effort_commit() -> str | None:
    """Short HEAD commit when running from a git checkout; never required."""
    import subprocess

    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(root), capture_output=True, text=True, timeout=3, check=False,
    )
    if result.returncode != 0:
        return None
    commit = (result.stdout or "").strip()
    return commit[:12] or None
