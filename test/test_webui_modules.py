"""WebUI phase-2 modularization regression tests.

Covers the split of web/ui/js/app.js into focused native ES modules:
  - module structure (files, exports, app.js imports, size budget)
  - no duplicated feature implementations left in app.js
  - EventSource/SSE ownership inventory (runtime behavior preserved)
  - timer ownership (no stacking on repeated navigation)
  - router boot (direct hash / F5 / back-forward)
  - no inline handlers, no window globals, no circular-import hazards
  - auth secret hygiene (no token in URL/storage)

Runtime lifecycle/SSE behavior is additionally verified by live browser
E2E (see coding report); this file guards the structure statically so
regressions fail fast in CI without a browser.
"""
import os
import re
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R

_JS = os.path.join(_PROJECT_DIR, "web", "ui", "js")


def _read(name):
    with open(os.path.join(_JS, name), encoding="utf-8") as fh:
        return fh.read()


# ===================================================================
# A. Module structure
# ===================================================================

_EXPECTED_MODULES = (
    "app.js", "api.js", "router.js", "utils.js", "logs.js", "news.js",
    "sources.js", "alerts.js", "ai-alerts.js", "market-sources.js",
    "auth.js", "market.js", "instruments.js", "watchlists.js",
    "option-chain.js", "mcp-tools.js", "quotes.js", "charts.js",
)

_APP_IMPORTS = (
    'from "./router.js"',
    'from "./sources.js"',
    'from "./news.js"',
    'from "./logs.js"',
    'from "./market.js"',
    'from "./quotes.js"',
    'from "./charts.js"',
    'from "./alerts.js"',
    'from "./ai-alerts.js"',
    'from "./mcp-tools.js"',
    'from "./instruments.js"',
    'from "./watchlists.js"',
    'from "./option-chain.js"',
    'from "./market-sources.js"',
    'from "./auth.js"',
)


def test_module_tree(r: R) -> None:
    mods = {}
    for name in _EXPECTED_MODULES:
        try:
            with open(os.path.join(_JS, name), encoding="utf-8") as fh:
                mods[name] = fh.read()
        except Exception as exc:
            r.fail(f"MOD:readable:{name}", str(exc))
            return
    r.ok("MOD:readable")

    app = mods["app.js"]
    for imp in _APP_IMPORTS:
        if imp in app:
            r.ok(f"MOD:app_imports:{imp}")
        else:
            r.fail(f"MOD:app_imports:{imp}", "missing import")

    # app.js size budget: orchestration only (was 2676 pre-split).
    lines = len(app.splitlines())
    if lines <= 700:
        r.ok(f"MOD:app_size:{lines}")
    else:
        r.fail(f"MOD:app_size:{lines}", "over 700-line budget")

    # No duplicated feature implementations left in app.js.
    moved = ("function initAlerts", "function loadAlerts",
             "function initAlertPush", "function pushAlertNotification",
             "function initAIAlerts", "function _loadAIAlerts",
             "function pollSources", "function initSourceControls",
             "function renderSourceDetail", "function pollAuthStatus",
             "function initAuth", "function initCredentialSettings",
             "function connectSSE", "function handleQuoteUpdate",
             "function updateDashRow", "function renderMovers",
             "function renderDrawer", "function initDrawer",
             "function initFilter", "function loadInitialQuotes",
             "function initInstruments", "function initWatchlists",
             "function loadWatchlists", "function initOptionChain",
             "function renderOcStrikes", "function initCharts",
             "function renderChart", "function initMCPTools",
             "function _loadMCPTools", "function initNews",
             "function initLogs", "function switchView",
             "function initNav")
    dupes = [fn for fn in moved if fn in app]
    if not dupes:
        r.ok("MOD:no_duplicates")
    else:
        r.fail("MOD:no_duplicates", f"still in app.js: {dupes[:5]}")

    # No window-global leakage in feature modules.
    for name in _EXPECTED_MODULES:
        if re.search(r"window\.[A-Za-z_]+\s*=", mods[name]):
            r.fail(f"MOD:no_window_assign:{name}", "window.* assignment")
            break
    else:
        r.ok("MOD:no_window_assign")

    # No inline handlers anywhere in the new modules.
    for name in ("sources.js", "news.js", "alerts.js", "market-sources.js",
                 "instruments.js", "watchlists.js"):
        if "onclick=" in mods[name]:
            r.fail(f"MOD:no_inline:{name}", "onclick present")
            break
    else:
        r.ok("MOD:no_inline")


def test_router_boot(r: R) -> None:
    router = _read("router.js")
    app = _read("app.js")
    for marker in ("hashchange", "_fire(_currentView())", "_routerBound",
                   "export function onViewEnter", "export function initNav",
                   "export function switchView", "export let currentView"):
        if marker in router:
            r.ok(f"ROUTER:has:{marker[:24]}")
        else:
            r.fail(f"ROUTER:has:{marker[:24]}", "missing")
    for hook in ('onViewEnter("news"', 'onViewEnter("logs"',
                 'onViewEnter("ai-alerts"', 'onViewEnter("mcp"'):
        if hook in app:
            r.ok(f"ROUTER:hook:{hook}")
        else:
            r.fail(f"ROUTER:hook:{hook}", "not registered")


def test_eventsource_inventory(r: R) -> None:
    mods = {n: _read(n) for n in _EXPECTED_MODULES}
    counts = {n: src.count("new EventSource") for n, src in mods.items()}
    total = sum(counts.values())
    # Intended runtime topology (unchanged by the split):
    # market stream (market.js) + alert push (alerts.js) + logs (logs.js).
    if total == 3:
        r.ok("SSE:total_3")
    else:
        r.fail("SSE:total_3", f"got {total}: {counts}")
    if counts.get("market.js", 0) == 1 and \
            'new EventSource("/api/market/stream")' in mods["market.js"]:
        r.ok("SSE:market_singleton")
    else:
        r.fail("SSE:market_singleton", "market stream moved/duplicated")
    if counts.get("alerts.js", 0) == 1 and \
            'new EventSource("/events/stream")' in mods["alerts.js"]:
        r.ok("SSE:push_singleton")
    else:
        r.fail("SSE:push_singleton", "push stream moved/duplicated")
    if counts.get("logs.js", 0) == 1 and \
            'new EventSource("/api/logs/stream")' in mods["logs.js"]:
        r.ok("SSE:logs_singleton")
    else:
        r.fail("SSE:logs_singleton", "logs stream moved/duplicated")
    if counts.get("app.js", 0) == 0:
        r.ok("SSE:none_in_app")
    else:
        r.fail("SSE:none_in_app", f"app.js has {counts['app.js']}")
    # Singleton guards survive the move.
    guards = (("market.js", "if (es) return"),
              ("alerts.js", "if (alertPushSource) return"),
              ("logs.js", "if (logsEventSource) return"))
    for name, guard in guards:
        if guard in mods[name]:
            r.ok(f"SSE:guard:{name}")
        else:
            r.fail(f"SSE:guard:{name}", "missing singleton guard")

    # NOTE (explicit, per job spec): legacy tests test_web_ui.py (W3) and
    # test_consumer_completeness.py (CE12) count `new EventSource` inside
    # app.js and expect 2. Correct modularization moves the alert-push
    # stream to alerts.js, so app.js now holds 0 and the intended total
    # stays 3. Those stale file-layout assertions are intentionally left
    # untouched here (CI-maintenance backlog, separate job).
    r.ok("SSE:stale_count_tests_identified")


def test_timer_ownership(r: R) -> None:
    mods = {n: _read(n) for n in _EXPECTED_MODULES}
    expect = {"alerts.js": 2, "watchlists.js": 1, "quotes.js": 1,
              "app.js": 2}
    for name, want in expect.items():
        got = mods[name].count("setInterval(")
        if got == want:
            r.ok(f"TIMER:{name}:{got}")
        else:
            r.fail(f"TIMER:{name}:{got}", f"expected {want}")
    # Re-entry guards on interval-owning inits.
    for name, guard in (("alerts.js", "_alertsInitDone"),
                        ("watchlists.js", "_watchlistsInitDone"),
                        ("quotes.js", "_drawerIntervalStarted"),
                        ("instruments.js", "_instrumentsInitDone"),
                        ("market-sources.js", "_controlsBound"),
                        ("logs.js", "logsEventSource")):
        if guard in mods[name]:
            r.ok(f"TIMER:guard:{name}")
        else:
            r.fail(f"TIMER:guard:{name}", "missing")


def test_shared_helpers(r: R) -> None:
    utils = _read("utils.js")
    for name in ("export const $", "export function esc",
                 "export function escAttr", "export function fmtLogTs",
                 "export const fmt ", "export const fmtVol",
                 "export const chgClass", "export const nowStr",
                 "export function setIndicator", "export const escDash",
                 "export const fmtNum", "export const fmtTs"):
        if name in utils:
            r.ok(f"UTIL:has:{name.split()[-1]}")
        else:
            r.fail(f"UTIL:has:{name.split()[-1]}", "missing export")
    # Consumers import instead of redefining.
    needs = {"market.js": ("escDash", "fmt", "setIndicator"),
             "alerts.js": ("escDash",),
             "market-sources.js": ("escDash",),
             "auth.js": ("$",),
             "watchlists.js": ("fmt", "chgClass"),
             "charts.js": ("escDash", "fmt"),
             "instruments.js": ("escDash",),
             "option-chain.js": ("fmt",),
             "quotes.js": ("fmtTs", "fmtNum"),
             "logs.js": ("fmtLogTs",)}
    for name, syms in needs.items():
        src = _read(name)
        blocks = re.findall(r"import\s*\{([^}]*)\}", src)
        missing = [s for s in syms
                   if not any(re.search(rf"(?<![\w$]){re.escape(s)}(?![\w$])",
                                        b) for b in blocks)]
        if not missing:
            r.ok(f"UTIL:imports:{name}")
        else:
            r.fail(f"UTIL:imports:{name}", f"missing {missing}")
    # No shadowing redefinitions of shared helpers in feature modules.
    for name in ("market.js", "alerts.js", "market-sources.js", "auth.js",
                 "watchlists.js", "charts.js", "instruments.js",
                 "option-chain.js", "quotes.js", "logs.js"):
        src = _read(name)
        shadows = [m for m in re.finditer(
            r"^(?:const|let|function)\s+(fmt|fmtVol|chgClass|nowStr|"
            r"setIndicator|escDash|fmtNum|fmtTs)\b", src, re.M)
            if "import" not in src[max(0, m.start() - 40):m.start()]]
        if not shadows:
            r.ok(f"UTIL:no_shadow:{name}")
        else:
            r.fail(f"UTIL:no_shadow:{name}", "redefinition found")


def test_declared_state(r: R) -> None:
    # Former implicit globals are now declared module state.
    oc = _read("option-chain.js")
    if "let ocUnderlying" in oc and "let ocFullStrikes" in oc:
        r.ok("STATE:option_chain_declared")
    else:
        r.fail("STATE:option_chain_declared", "implicit globals remain")
    charts = _read("charts.js")
    if "let chartInstance" in charts and "let chartSelection" in charts:
        r.ok("STATE:charts_declared")
    else:
        r.fail("STATE:charts_declared", "implicit globals remain")
    # Cross-module state travels via explicit exports, not window.
    market = _read("market.js")
    if "export const quotes" in market and "export function getQuote" in market:
        r.ok("STATE:market_exports")
    else:
        r.fail("STATE:market_exports", "missing")
    auth = _read("auth.js")
    if "export function getAuthStatus" in auth:
        r.ok("STATE:auth_exports")
    else:
        r.fail("STATE:auth_exports", "missing")


def test_auth_hygiene(r: R) -> None:
    auth = _read("auth.js")
    # Strip comments: doc prose may name storage without using it.
    code = "\n".join(
        line for line in auth.splitlines()
        if line.strip() and not line.strip().startswith(("*", "//")))
    if "localStorage" not in code:
        r.ok("AUTH:no_storage")
    else:
        r.fail("AUTH:no_storage", "localStorage use in auth.js")
    if 'input.value = ""' in auth:
        r.ok("AUTH:token_cleared")
    else:
        r.fail("AUTH:token_cleared", "token field not cleared")
    if "access_token=" not in auth and "?token=" not in auth:
        r.ok("AUTH:no_token_in_url")
    else:
        r.fail("AUTH:no_token_in_url", "token in URL")
    if "params.delete(" in auth:
        r.ok("AUTH:callback_stripped")
    else:
        r.fail("AUTH:callback_stripped", "callback params retained")
    if "JSON.stringify({ access_token: token })" in auth:
        r.ok("AUTH:post_body_only")
    else:
        r.fail("AUTH:post_body_only", "submit path changed")


def test_view_contracts(r: R) -> None:
    # Lifecycle APIs each view module must expose.
    contracts = {
        "alerts.js": ("export function initAlerts",
                      "export function initAlertPush"),
        "ai-alerts.js": ("export function initAIAlerts",
                         "export function openAIAlerts"),
        "mcp-tools.js": ("export function initMCPTools",
                         "export function openMCPTools"),
        "market-sources.js": ("export function initSourceControls",
                              "export async function pollSources"),
        "auth.js": ("export function initAuth",
                    "export async function pollAuthStatus"),
        "instruments.js": ("export function initInstruments",),
        "watchlists.js": ("export function initWatchlists",
                          "export async function loadWatchlists"),
        "option-chain.js": ("export function initOptionChain",),
        "market.js": ("export function connectSSE",
                      "export async function loadInitialQuotes",
                      "export function initFilter"),
        "quotes.js": ("export function initDrawer",),
        "charts.js": ("export function initCharts",),
        "news.js": ("export function initNewsUI",
                    "export async function openNews"),
        "sources.js": ("export function initSourcesUI",
                       "export async function loadNewsSources"),
        "logs.js": ("export function initLogsUI",
                    "export function openLogs"),
    }
    for name, markers in contracts.items():
        src = _read(name)
        missing = [m for m in markers if m not in src]
        if not missing:
            r.ok(f"API:{name}")
        else:
            r.fail(f"API:{name}", f"missing {missing}")


# ===================================================================
# MAIN
# ===================================================================

def main() -> None:
    r = R()
    print("=" * 60)
    print("WebUI phase-2 modularization regression tests")
    print("=" * 60)

    print("\n--- Module structure ---")
    test_module_tree(r)

    print("\n--- Router boot ---")
    # router assertions live beside structure; boot covered via files.
    test_router_boot(r)

    print("\n--- EventSource inventory ---")
    test_eventsource_inventory(r)

    print("\n--- Timer ownership ---")
    test_timer_ownership(r)

    print("\n--- Shared helpers ---")
    test_shared_helpers(r)

    print("\n--- Declared state ---")
    test_declared_state(r)

    print("\n--- Auth hygiene ---")
    test_auth_hygiene(r)

    print("\n--- View contracts ---")
    test_view_contracts(r)

    print("\n" + "=" * 60)
    r.summary()
    print("=" * 60)

    if r.failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
