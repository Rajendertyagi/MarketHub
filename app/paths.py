"""
Canonical project path resolution for EventHub.

Single owner for filesystem anchors. Runtime artifacts (data/, logs/,
config.json) always resolve against the PROJECT ROOT — never against the
package directory — so moving modules deeper cannot relocate user data.

No sys.path manipulation; import resolution relies on running from the
project root (python -m app.server) or normal package installation.
"""

from __future__ import annotations

from pathlib import Path

# D:\Temp\MCPEvent  (app/paths.py -> parent = app/ -> parent = root)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONFIG_PATH = PROJECT_ROOT / "config.json"
DATA_ROOT = PROJECT_ROOT / "data"
LOGS_ROOT = PROJECT_ROOT / "logs"


def ensure_dir(path: Path) -> Path:
    """Create ``path`` (parents included) if missing; return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path
