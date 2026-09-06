"""
Architecture boundary guardrails for MarketHub.

Static, AST-based import checks. These are intentionally narrow: every rule
below is GREEN on the current tree, so the test protects the future without
being a brittle gate. Add new edges ONLY when they are already clean.

Policy (see docs/ARCHITECTURE_BOUNDARIES.md):
  - OPTIONS analytics must not depend on auth/secrets/broker internals/config.
  - NEWS must not depend on broker auth/secrets/config/server.
  - The WebUI (web/ui/js) must never import broker/provider code.

Run with:  pytest test/test_architecture_boundaries.py
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Modules that must never be imported by the listed "from" subtrees.
FORBIDDEN_EDGES = [
    # OPTIONS -> auth / secrets / broker adapter / api / config
    {
        "within": "market/analytics",
        "forbid": (
            "app.secrets_store",
            "brokers.upstox.auth",
            "brokers.fyers.auth",
            "api.routes",
            "app.config",
        ),
        "why": "Options analytics must depend only on canonical market models.",
    },
    # NEWS -> broker auth / secrets / api / config / server
    {
        "within": "news",
        "forbid": (
            "brokers.upstox.auth",
            "brokers.fyers.auth",
            "app.secrets_store",
            "api.routes",
            "app.config",
            "app.server",
        ),
        "why": "News must not reach into broker auth, secrets, or startup.",
    },
]


def _module_imports(path: pathlib.Path) -> list[str]:
    """Return all absolute module names imported by a Python file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return []
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.append(node.module)
    return mods


def _py_files(within: str):
    base = ROOT / within
    if not base.exists():
        return
    for f in base.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        yield f


def test_options_analytics_does_not_import_protected_modules():
    edge = next(e for e in FORBIDDEN_EDGES if e["within"] == "market/analytics")
    violations = []
    for f in _py_files(edge["within"]):
        for mod in _module_imports(f):
            if any(mod == fb or mod.startswith(fb + ".") for fb in edge["forbid"]):
                violations.append((str(f.relative_to(ROOT)), mod))
    assert not violations, (
        f"OPTIONS analytics must not import protected modules ({edge['why']}): "
        f"{violations}"
    )


def test_news_does_not_import_protected_modules():
    edge = next(e for e in FORBIDDEN_EDGES if e["within"] == "news")
    violations = []
    for f in _py_files(edge["within"]):
        for mod in _module_imports(f):
            if any(mod == fb or mod.startswith(fb + ".") for fb in edge["forbid"]):
                violations.append((str(f.relative_to(ROOT)), mod))
    assert not violations, (
        f"NEWS must not import protected modules ({edge['why']}): {violations}"
    )


def test_webui_never_imports_broker_provider_code():
    """Provider-specific code must never leak into the WebUI."""
    webui = ROOT / "web" / "ui" / "js"
    if not webui.exists():
        return
    violations = []
    for f in webui.rglob("*.js"):
        if "__pycache__" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            s = line.strip()
            if not (s.startswith("import ") or s.startswith("from ")):
                continue
            if "brokers" in s or "/brokers" in s or "broker/" in s:
                violations.append((str(f.relative_to(ROOT)), s))
    assert not violations, (
        f"WebUI must never import broker/provider code: {violations}"
    )
