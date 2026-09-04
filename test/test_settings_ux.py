"""Settings UX + News space refactor regression tests.

Covers the Settings sidebar workspace and the News content-first layout:
  A. settings navigation (sidebar, panels, deep links, boot, guards)
  B. news page slim (no CRUD, filters kept, manage link, history)
  C. news-sources settings (single CRUD owner, validation, errors)
  D. market sources under settings (render, polling singleton)
  E. brokers (controls work, secret hygiene unchanged)
  F. ai/mcp settings (provider form, backend untouched)
  G. backup/general extraction
  H. app.js budget (no extracted logic remains, imports clean)
  I. router sub-route support

Live navigation/rendering is additionally verified by browser E2E
(see coding report); this file guards structure + contracts in CI.
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

_UI = os.path.join(_PROJECT_DIR, "web", "ui")
_JS = os.path.join(_UI, "js")
_SET = os.path.join(_JS, "features", "settings")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _js(name):
    return _read(os.path.join(_JS, name))


def _feat(name):
    return _read(os.path.join(_SET, name))


_SECTIONS = ("general", "brokers", "news-sources", "market-sources",
             "alerts", "ai-mcp", "data-retention", "logging", "backup")


# ===================================================================
# A. Settings navigation
# ===================================================================

def test_settings_shell(r: R) -> None:
    html = _read(os.path.join(_UI, "index.html"))
    for sec in _SECTIONS:
        if f'data-section="{sec}"' in html:
            r.ok(f"SET:nav:{sec}")
        else:
            r.fail(f"SET:nav:{sec}", "sidebar button missing")
        if f'id="set-panel-{sec}"' in html:
            r.ok(f"SET:panel:{sec}")
        else:
            r.fail(f"SET:panel:{sec}", "panel missing")
    # Exactly one panel visible by default; the rest start hidden.
    m = re.search(r'<div class="set-panel" id="set-panel-general">', html)
    if m:
        r.ok("SET:general_default_visible")
    else:
        r.fail("SET:general_default_visible", "general panel markup changed")
    hidden = [s for s in _SECTIONS if s != "general"
              if f'class="set-panel hidden" id="set-panel-{s}"' in html]
    if len(hidden) == len(_SECTIONS) - 1:
        r.ok("SET:others_hidden")
    else:
        r.fail("SET:others_hidden", f"only {len(hidden)} hidden")

    idx = _read(os.path.join(_SET, "index.js"))
    for marker in ("showSettingsSection", "initSettingsUI", "hashchange",
                   "_settingsBound", "_sectionFromHash",
                   "#/settings/", "switchView"):
        if marker in idx:
            r.ok(f"SET:idx:{marker}")
        else:
            r.fail(f"SET:idx:{marker}", "missing")
    # Section allowlist matches the sidebar exactly.
    if all(f'"{s}"' in idx for s in _SECTIONS):
        r.ok("SET:allowlist")
    else:
        r.fail("SET:allowlist", "section list drifted")
    # Deep-link shapes the router understands.
    if "#/settings/" in idx and "replaceState" in idx:
        r.ok("SET:deep_link_write")
    else:
        r.fail("SET:deep_link_write", "hash write missing")


def test_router_subroutes(r: R) -> None:
    router = _js("router.js")
    if 'view.indexOf("/")' in router or 'indexOf("/")' in router:
        r.ok("ROUTER:subpath_split")
    else:
        r.fail("ROUTER:subpath_split", "no sub-path handling")
    if "baseOf" in router or "slice(0," in router:
        r.ok("ROUTER:base_lookup")
    else:
        r.fail("ROUTER:base_lookup", "initNav base lookup missing")
    # Base-segment hooks fire alongside exact-view hooks.
    if "slice(0, slash)" in router or "base" in router:
        r.ok("ROUTER:base_fallback")
    else:
        r.fail("ROUTER:base_fallback", "no base fallback")
    # Back/forward: hash-only changes must activate the base view,
    # otherwise the URL and the visible view disagree.
    if ".view.active" in router and "switchView(v)" in router:
        r.ok("ROUTER:hash_activates_view")
    else:
        r.fail("ROUTER:hash_activates_view", "hashchange never switches view")


# ===================================================================
# B. News content-first
# ===================================================================

def _news_section(html):
    start = html.find('id="view-news"')
    end = html.find("LOGS VIEW", start)
    return html[start:end if end > 0 else len(html)]


def test_news_slim(r: R) -> None:
    html = _read(os.path.join(_UI, "index.html"))
    news = _news_section(html)
    for gone in ("news-sources-panel", "news-source-modal", "news-add-source",
                  "news-test-source", "news-modal-save", "news-src-id",
                  # N-UI1: standalone sentiment dashboard panel is gone
                  # (sentiment is row/reader/strip metadata now).
                  "news-sentiment-btn", "news-sentiment-result"):
        if gone not in news:
            r.ok(f"NEWS:removed:{gone}")
        else:
            r.fail(f"NEWS:removed:{gone}", "CRUD still on News page")
    for kept in ("news-filter-source", "news-filter-category",
                  "news-filter-keywords", "news-filter-symbol",
                  "news-filter-max-age", "news-refresh",
                  "news-articles-list",
                  # N-UI1 3-pane shell ids.
                  "news-shell", "news-source-list", "news-reader",
                  "news-agg", "news-new-pill"):
        if kept in news:
            r.ok(f"NEWS:kept:{kept}")
        else:
            r.fail(f"NEWS:kept:{kept}", "missing from News page")
    if 'href="#/settings/news-sources"' in news:
        r.ok("NEWS:manage_link")
    else:
        r.fail("NEWS:manage_link", "Manage Sources link missing")
    # N-UI1: filter wiring lives in the news feature modules; news.js is
    # a thin compat shim delegating to features/news/index.js.
    feat = _read(os.path.join(_JS, "features", "news", "feeds.js"))
    idx = _read(os.path.join(_JS, "features", "news", "index.js"))
    if "news-filter-category" in feat and "news-filter-max-age" in feat:
        r.ok("NEWS:toolbar_wired")
    else:
        r.fail("NEWS:toolbar_wired", "category/max-age not read")
    if "max_age_hours" in idx and "categories" in idx:
        r.ok("NEWS:params_sent")
    else:
        r.fail("NEWS:params_sent", "filter params missing")


# ===================================================================
# C. News-sources settings (single owner)
# ===================================================================

def test_news_sources_settings(r: R) -> None:
    html = _read(os.path.join(_UI, "index.html"))
    panel_start = html.find('id="set-panel-news-sources"')
    panel = html[panel_start:panel_start + 9000]
    for ident in ("news-sources-body", "news-source-modal", "news-add-source",
                  "news-modal-save", "news-test-source", "news-src-id",
                  "news-src-type", "news-src-url", "news-src-subreddit"):
        if ident in panel:
            r.ok(f"NSRC:present:{ident}")
        else:
            r.fail(f"NSRC:present:{ident}", "missing from settings panel")
    # IDs exist exactly once in the whole document (no duplication).
    for ident in ("news-sources-body", "news-source-modal", "news-src-id"):
        if html.count(f'id="{ident}"') == 1:
            r.ok(f"NSRC:unique:{ident}")
        else:
            r.fail(f"NSRC:unique:{ident}", "duplicated or missing")
    # sources.js remains the single CRUD owner.
    src = _js("sources.js")
    for fn in ("loadNewsSources", "_renderNewsSources", "_saveSource",
               "_testSource", "_newsToggle", "_newsEdit", "_newsDelete",
               "_onNewsActionClick"):
        if fn in src:
            r.ok(f"NSRC:owner:{fn}")
        else:
            r.fail(f"NSRC:owner:{fn}", "not in sources.js")
    # No second CRUD implementation anywhere in the new settings files.
    for name in ("index.js", "general.js", "ai-mcp.js", "backup.js"):
        body = _feat(name)
        if "_saveSource" not in body and "news-sources-body" not in body:
            r.ok(f"NSRC:single_owner:{name}")
        else:
            r.fail(f"NSRC:single_owner:{name}", "duplicated CRUD logic")


# ===================================================================
# D. Market sources under settings
# ===================================================================

def test_market_sources_settings(r: R) -> None:
    html = _read(os.path.join(_UI, "index.html"))
    panel_start = html.find('id="set-panel-market-sources"')
    panel = html[panel_start:panel_start + 3000]
    for ident in ("upstox-src-detail", "upstox-src-msg",
                  "fyers-src-detail", "fyers-src-msg"):
        if ident in panel:
            r.ok(f"MSRC:present:{ident}")
        else:
            r.fail(f"MSRC:present:{ident}", "detail block missing")
    src = _js("market-sources.js")
    if 'startsWith("settings/")' in src or "startsWith('settings/')" in src:
        r.ok("MSRC:settings_aware_poll")
    else:
        r.fail("MSRC:settings_aware_poll", "poll ignores subsections")
    if src.count("setInterval(") == 0:
        r.ok("MSRC:no_own_timer")
    else:
        r.fail("MSRC:no_own_timer", "module-local polling loop added")


# ===================================================================
# E. Brokers
# ===================================================================

def test_brokers(r: R) -> None:
    html = _read(os.path.join(_UI, "index.html"))
    panel_start = html.find('id="set-panel-brokers"')
    panel = html[panel_start:panel_start + 12000]
    for ident in ("cred-save", "cred-delete", "auth-submit",
                  "oauth-login-btn", "fyers-save", "fyers-login-btn",
                  "fyers-pin", "auth-token-input"):
        if ident in panel:
            r.ok(f"BROKER:present:{ident}")
        else:
            r.fail(f"BROKER:present:{ident}", "missing from Brokers panel")
    auth = _js("auth.js")
    if "export function initFyers" in auth:
        r.ok("BROKER:fyers_in_auth")
    else:
        r.fail("BROKER:fyers_in_auth", "initFyers not in auth.js")
    # Secret hygiene unchanged (code surfaces only).
    code = "\n".join(
        line for line in auth.splitlines()
        if line.strip() and not line.strip().startswith(("*", "//")))
    if "localStorage" not in code:
        r.ok("BROKER:no_storage")
    else:
        r.fail("BROKER:no_storage", "localStorage use")
    if code.count("input.value = \"\"") >= 2 or 'value = ""' in code:
        r.ok("BROKER:fields_cleared")
    else:
        r.fail("BROKER:fields_cleared", "secret fields not cleared")
    if "access_token=" not in code and "?token=" not in code:
        r.ok("BROKER:no_token_in_url")
    else:
        r.fail("BROKER:no_token_in_url", "token in URL")


# ===================================================================
# F. AI/MCP settings
# ===================================================================

def test_ai_mcp(r: R) -> None:
    html = _read(os.path.join(_UI, "index.html"))
    panel_start = html.find('id="set-panel-ai-mcp"')
    panel = html[panel_start:panel_start + 6000]
    for ident in ("ai-save", "ai-endpoint", "ai-model", "ai-key",
                  "ai-message"):
        if ident in panel:
            r.ok(f"AIMCP:present:{ident}")
        else:
            r.fail(f"AIMCP:present:{ident}", "missing from AI/MCP panel")
    if 'href="#/mcp"' in panel:
        r.ok("AIMCP:mcp_link")
    else:
        r.fail("AIMCP:mcp_link", "MCP page link missing")
    mod = _feat("ai-mcp.js")
    if "export function initAIMCPSettings" in mod:
        r.ok("AIMCP:owner")
    else:
        r.fail("AIMCP:owner", "initAIMCPSettings missing")
    if 'document.getElementById("ai-key").value = ""' in mod:
        r.ok("AIMCP:key_cleared")
    else:
        r.fail("AIMCP:key_cleared", "API key field not cleared")


# ===================================================================
# G. Backup / general extraction
# ===================================================================

def test_backup_general(r: R) -> None:
    html = _read(os.path.join(_UI, "index.html"))
    bpanel = html[html.find('id="set-panel-backup"'):
                  html.find('id="set-panel-backup"') + 2000]
    if "db-backup" in bpanel and "backup-message" in bpanel:
        r.ok("BKUP:present")
    else:
        r.fail("BKUP:present", "backup controls misplaced")
    mod = _feat("backup.js")
    if "export function initBackupSettings" in mod \
            and "/api/admin/backup" in mod:
        r.ok("BKUP:owner")
    else:
        r.fail("BKUP:owner", "backup logic missing")
    gpanel = html[html.find('id="set-panel-general"'):
                  html.find('id="set-panel-general"') + 6000]
    if "app-save" in gpanel and "app-base-url-input" in gpanel:
        r.ok("GEN:present")
    else:
        r.fail("GEN:present", "app settings misplaced")
    gen = _feat("general.js")
    if "export function initGeneralSettings" in gen:
        r.ok("GEN:owner")
    else:
        r.fail("GEN:owner", "initGeneralSettings missing")


# ===================================================================
# H. app.js budget
# ===================================================================

def test_app_budget(r: R) -> None:
    app = _js("app.js")
    lines = len(app.splitlines())
    if lines <= 400:
        r.ok(f"APP:size:{lines}")
    else:
        r.fail(f"APP:size:{lines}", "budget exceeded")
    moved = ("function initFyers", "function initBackup",
             "function initAppSettings", "function initAIProvider",
             "function pollSources", "function initAlerts",
             "function initAIAlerts", "function initMCPTools",
             "function connectSSE", "function initDrawer",
             "function initInstruments", "function initWatchlists",
             "function initOptionChain", "function initCharts",
             "function switchView", "function initNav",
             "function initNews", "function initLogs")
    dupes = [fn for fn in moved if fn in app]
    if not dupes:
        r.ok("APP:no_feature_dupes")
    else:
        r.fail("APP:no_feature_dupes", f"remain: {dupes[:4]}")
    for imp in ('./features/settings/index.js',
                './features/settings/general.js',
                './features/settings/ai-mcp.js',
                './features/settings/backup.js'):
        if imp in app:
            r.ok(f"APP:imports:{imp.split('/')[-1]}")
        else:
            r.fail(f"APP:imports:{imp.split('/')[-1]}", "missing import")


# ===================================================================
# Visual structure (regression guards for live-found defects)
# ===================================================================

def test_settings_dom_balance(r: R) -> None:
    """Every settings panel's divs balance; layout nesting is intact.

    Guards the live-found bug where one extra </div> ejected six panels
    out of .settings-content (broke narrow layout + panel context).
    """
    import re
    html = _read(os.path.join(_UI, "index.html"))
    seg = html[html.find('id="view-settings"'):html.find("NEWS VIEW")]
    opens = len(re.findall(r"<div\b", seg))
    closes = len(re.findall(r"</div>", seg))
    if opens == closes:
        r.ok(f"DOM:settings_balanced:{opens}")
    else:
        r.fail("DOM:settings_balanced", f"open={opens} close={closes}")
    seg_n = html[html.find('id="view-news"'):html.find("LOGS VIEW")]
    opens_n = len(re.findall(r"<div\b", seg_n))
    closes_n = len(re.findall(r"</div>", seg_n))
    if opens_n == closes_n:
        r.ok(f"DOM:news_balanced:{opens_n}")
    else:
        r.fail("DOM:news_balanced", f"open={opens_n} close={closes_n}")


def test_responsive_rules(r: R) -> None:
    """Narrow-viewport CSS contract: column layout, wrapped nav/toolbar,
    in-panel table scroll, no global horizontal breakage from settings."""
    css = _read(os.path.join(_UI, "css", "style.css"))
    for marker in ("@media (max-width: 760px)",
                   ".settings-layout { flex-direction: column; align-items: stretch; }",
                   ".table-scroll { overflow-x: auto; }",
                   # N-UI1: old stacked toolbar replaced by wrapping strip.
                   ".news-strip",
                   "flex-wrap: wrap"):
        if marker in css:
            r.ok(f"CSS:has:{marker[:28]}")
        else:
            r.fail(f"CSS:has:{marker[:28]}", "missing")
    html = _read(os.path.join(_UI, "index.html"))
    if 'class="table-scroll"' in html and 'class="news-strip"' in html:
        r.ok("CSS:wired")
    else:
        r.fail("CSS:wired", "scroll/strip wrappers missing")

def main() -> None:
    r = R()
    print("=" * 60)
    print("Settings UX + News space refactor regression tests")
    print("=" * 60)

    print("\n--- A Settings navigation ---")
    test_settings_shell(r)
    test_router_subroutes(r)

    print("\n--- B News content-first ---")
    test_news_slim(r)

    print("\n--- C News-sources settings ---")
    test_news_sources_settings(r)

    print("\n--- D Market sources ---")
    test_market_sources_settings(r)

    print("\n--- E Brokers ---")
    test_brokers(r)

    print("\n--- F AI/MCP ---")
    test_ai_mcp(r)

    print("\n--- G Backup/General ---")
    test_backup_general(r)

    print("\n--- H app.js budget ---")
    test_app_budget(r)

    print("\n--- Visual structure ---")
    test_settings_dom_balance(r)
    test_responsive_rules(r)

    print("\n" + "=" * 60)
    r.summary()
    print("=" * 60)

    if r.failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
