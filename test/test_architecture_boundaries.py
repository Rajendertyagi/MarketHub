"""
Architecture boundary guardrails for MarketHub.

Static, AST-based import checks. These are intentionally narrow: every rule
below is GREEN on the current tree, so the test protects the future without
being a brittle gate. Add new edges ONLY when they are already clean.

Policy (see docs/ARCHITECTURE_BOUNDARIES.md):
  - OPTIONS analytics must not depend on auth/secrets/broker internals/config.
  - NEWS must not depend on broker auth/secrets/config/server.
    - The WebUI (frontend/src) must never import broker/provider code.
  - Config loading (app/config.py) must stay non-secret and adapter-free.
  - Canonical market data must not depend on transport/UI/adapters.
  - Core is a lower layer and must not depend on app composition.

Run with:  pytest test/test_architecture_boundaries.py
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Modules that must never be imported by the listed "within" subtree or file.
# `within` may be a package directory or a single file.
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
    # CONFIG must stay non-secret and adapter-free.
    {
        "within": "app/config.py",
        "forbid": ("app.secrets_store", "brokers"),
        "why": "Config loading must not pull in secrets or broker adapters.",
    },
    # CANONICAL MARKET must not depend on transport / UI / adapters.
    {
        "within": "market",
        "forbid": ("web", "api", "app", "brokers"),
        "why": "Canonical market data must be transport- and adapter-agnostic.",
    },
    # CORE is a lower layer; must not depend on app composition.
    {
        "within": "core",
        "forbid": ("app",),
        "why": "Core must not depend on the application composition root.",
    },
    # AUTH OWNERSHIP FREEZE: feature/service layers must not reach auth.
    {
        "within": "market",
        "forbid": (
            "app.auth",
            "app.secrets_store",
            "app.fyers_runtime_auth",
            "brokers.upstox.auth",
            "brokers.fyers.auth",
        ),
        "why": "Market layers must not own, read, or compute broker sessions.",
    },
    {
        "within": "news",
        "forbid": (
            "app.auth",
            "app.fyers_runtime_auth",
        ),
        "why": "News must not reach the auth subsystem or runtime tokens.",
    },
    {
        "within": "mcp_server",
        "forbid": (
            "app.auth",
            "app.secrets_store",
            "app.fyers_runtime_auth",
            "brokers.upstox.auth",
            "brokers.fyers.auth",
            "api.routes",
            "api.product_routes",
            "app.server",
        ),
        "why": "MCP serves canonical services only, never broker sessions.",
    },
    {
        "within": "sources",
        "forbid": (
            "app.auth",
            "app.secrets_store",
            "app.fyers_runtime_auth",
            "api.routes",
            "api.product_routes",
            "app.server",
        ),
        "why": "Source lifecycle consumes credentials; it must not own sessions.",
    },
    # AUTH stays low-level: it coordinates store + broker adapters only.
    {
        "within": "app/auth",
        "forbid": (
            "api",
            "app.server",
            "sources",
            "market",
            "news",
            "mcp_server",
            "web",
            "core",
            "brokers.upstox.feed",
            "brokers.upstox.rest",
            "brokers.fyers.feed",
        ),
        "why": "Auth must not depend on transport, feeds, or feature layers.",
    },
    # ROUTES stay thin: no broker-adapter internals (services own them).
    {
        "within": "api/routes.py",
        "forbid": ("brokers",),
        "why": "Auth/settings routes must call services, never broker code.",
    },
    {
        "within": "api/product_routes.py",
        "forbid": ("brokers",),
        "why": "Product routes must call services, never broker code.",
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
    if base.is_file():
        if "__pycache__" not in base.parts:
            yield base
        return
    if not base.exists():
        return
    for f in base.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        yield f


def _violations(edge: dict) -> list[tuple[str, str]]:
    out = []
    for f in _py_files(edge["within"]):
        for mod in _module_imports(f):
            if any(mod == fb or mod.startswith(fb + ".") for fb in edge["forbid"]):
                out.append((str(f.relative_to(ROOT)), mod))
    return out


def _edge(within: str, index: int = 0) -> dict:
    matches = [e for e in FORBIDDEN_EDGES if e["within"] == within]
    return matches[index]


def test_options_analytics_does_not_import_protected_modules():
    edge = _edge("market/analytics")
    v = _violations(edge)
    assert not v, f"OPTIONS analytics must not import protected modules ({edge['why']}): {v}"


def test_news_does_not_import_protected_modules():
    edge = _edge("news")
    v = _violations(edge)
    assert not v, f"NEWS must not import protected modules ({edge['why']}): {v}"


def test_market_never_touches_broker_sessions():
    edge = _edge("market")
    v = _violations(edge)
    assert not v, f"Market layers must not touch broker sessions ({edge['why']}): {v}"


def test_news_never_touches_auth_subsystem():
    edge = _edge("news", 1)
    v = _violations(edge)
    assert not v, f"News must not touch auth ({edge['why']}): {v}"


def test_mcp_never_touches_broker_sessions():
    edge = _edge("mcp_server")
    v = _violations(edge)
    assert not v, f"MCP must not touch broker sessions ({edge['why']}): {v}"


def test_sources_never_own_sessions():
    edge = _edge("sources")
    v = _violations(edge)
    assert not v, f"Sources must not own sessions ({edge['why']}): {v}"


def test_auth_stays_low_level():
    edge = _edge("app/auth")
    v = _violations(edge)
    assert not v, f"Auth must stay low-level ({edge['why']}): {v}"


def test_auth_routes_never_import_brokers():
    edge = _edge("api/routes.py")
    v = _violations(edge)
    assert not v, f"Auth routes must not import brokers ({edge['why']}): {v}"


def test_product_routes_never_import_brokers():
    edge = _edge("api/product_routes.py")
    v = _violations(edge)
    assert not v, f"Product routes must not import brokers ({edge['why']}): {v}"


def test_config_is_non_secret_and_adapter_free():
    edge = next(e for e in FORBIDDEN_EDGES if e["within"] == "app/config.py")
    v = _violations(edge)
    assert not v, f"app/config.py must not import secrets/adapters ({edge['why']}): {v}"


def test_canonical_market_does_not_depend_on_transport_or_adapters():
    edge = next(e for e in FORBIDDEN_EDGES if e["within"] == "market")
    v = _violations(edge)
    assert not v, f"Canonical market must not depend on transport/UI/adapters ({edge['why']}): {v}"


def test_core_does_not_depend_on_app():
    edge = next(e for e in FORBIDDEN_EDGES if e["within"] == "core")
    v = _violations(edge)
    assert not v, f"Core must not depend on app composition ({edge['why']}): {v}"


def test_webui_never_imports_broker_provider_code():
    """Provider-specific code must never leak into the WebUI.

    The WebUI is now the React app in frontend/src (TypeScript). It must only
    talk to the backend through the API layer — never import broker/provider
    implementation modules directly.
    """
    webui = ROOT / "frontend" / "src"
    if not webui.exists():
        return
    violations = []
    for ext in ("*.ts", "*.tsx"):
        for f in webui.rglob(ext):
            if "__pycache__" in f.parts or "node_modules" in f.parts:
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                s = line.strip()
                if not (s.startswith("import ") or s.startswith("from ")):
                    continue
                if "brokers" in s or "/brokers" in s or "broker/" in s:
                    violations.append((str(f.relative_to(ROOT)), s))
    assert not violations, f"WebUI must never import broker/provider code: {violations}"


# ---------------------------------------------------------------------------
# AUTH SESSION-LIFECYCLE FREEZE (mechanical, AST-based — no line numbers)
#
# Broker session lifecycle (persist / restore / clear / invalidate /
# runtime-token install / expiry derivation) may ONLY be touched by:
#   * app/auth/*            — the single lifecycle owner
#   * app/secrets_store.py  — defines the durable ops
#   * app/fyers_runtime_auth.py — defines the Fyers runtime owner
#   * brokers/upstox/feed.py — defines update_credentials/auth_rejection
#   * brokers/upstox/rest.py — upstox_token_expiry at exchange time only
#   * app/server.py         — get/has_access_token READS for feed wiring
# Test doubles (test/*) are excluded: the harness must drive the lifecycle
# with isolated temp stores. Notably api/routes.py, api/product_routes.py
# and app/server.py must NOT appear here at all — routes are thin adapters
# and the server is composition wiring only.
# ---------------------------------------------------------------------------

AUTH_SESSION_ATTRS = frozenset({
    # Upstox durable session.
    "save_upstox_session_token", "load_upstox_session_token",
    "clear_upstox_session_token",
    # Fyers durable session material.
    "save_fyers_access_token", "load_fyers_access_token",
    "clear_fyers_session", "save_fyers_refresh_token",
    "load_fyers_refresh_token", "save_fyers_pin", "load_fyers_pin",
    # PIN-mode auth code custody.
    "save_upstox_auth_code", "load_upstox_auth_code",
    "clear_upstox_auth_code",
    # Forensic auth marker.
    "save_last_auth_status", "load_last_auth_status",
    # Canonical expiry derivation.
    "upstox_token_expiry",
    # Runtime-token install / ownership.
    "update_credentials", "set_access_token", "clear_access_token",
    "auth_rejection",
})

# (relative path, attribute) pairs allowed outside app/auth/*.
AUTH_ATTR_ALLOW = frozenset({
    ("brokers/upstox/rest.py", "upstox_token_expiry"),
    ("app/server.py", "get_access_token"),
    ("app/server.py", "has_access_token"),
})

_AUTH_OWNER_PREFIXES = (
    "app/auth/",
    "app/secrets_store.py",
    "app/fyers_runtime_auth.py",
    "brokers/upstox/feed.py",
)


def _session_attr_violations() -> list[tuple[str, str, int]]:
    out = []
    for f in ROOT.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        rel = str(f.relative_to(ROOT)).replace("\\", "/")
        if rel.startswith("test/"):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in AUTH_SESSION_ATTRS:
                if rel.startswith(_AUTH_OWNER_PREFIXES):
                    continue
                if (rel, node.attr) in AUTH_ATTR_ALLOW:
                    continue
                out.append((rel, node.attr, node.lineno))
    return out


def test_auth_session_lifecycle_is_owned():
    v = _session_attr_violations()
    assert not v, (
        "Broker session lifecycle leaked outside app/auth "
        "(persist/restore/clear/invalidate/runtime-install/expiry): "
        f"{v}"
    )


# Runtime-token constructors / factories (alias-aware): only the composition
# root, the auth subsystem, broker adapters, and tests may reference them.
AUTH_CONSTRUCTORS = frozenset({
    "CredentialStore", "FyersRuntimeAuth", "UpstoxAuthService",
    "FyersAuthService", "AuthService", "AuthStorage",
})

_AUTH_CTOR_ALLOW_PREFIXES = (
    "app/auth/",
    "app/secrets_store.py",       # defines CredentialStore
    "app/fyers_runtime_auth.py",  # defines FyersRuntimeAuth
    "app/server.py",              # composition root: builds + wires owners
    "api/routes.py",              # test/isolation seam: builds service over store
    "api/product_routes.py",      # test/isolation seam: builds service over store
    "brokers/",                   # adapters reference their own value objects
    "sources/registry.py",        # adapter boundary: builds feed credentials
)


def _constructor_violations() -> list[tuple[str, str, int]]:
    out = []
    for f in ROOT.rglob("*.py"):
        if "__pycache__" in f.parts:
            continue
        rel = str(f.relative_to(ROOT)).replace("\\", "/")
        if rel.startswith("test/"):
            continue
        if rel.startswith(_AUTH_CTOR_ALLOW_PREFIXES):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in AUTH_CONSTRUCTORS:
                out.append((rel, node.id, node.lineno))
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    base = alias.name.split(".")[0]
                    if alias.name in AUTH_CONSTRUCTORS or base in AUTH_CONSTRUCTORS:
                        out.append((rel, alias.name, node.lineno))
    return out


def test_auth_constructors_are_owned():
    v = _constructor_violations()
    assert not v, (
        "Auth owner construction leaked outside composition/auth/tests: "
        f"{v}"
    )
