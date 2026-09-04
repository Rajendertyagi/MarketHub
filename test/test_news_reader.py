"""N-UI1 3-column News reader regression tests.

Covers the News feature split + 3-pane contracts:
  A. feature module tree + ownership (state.js sole mutator)
  B. news.js thin compat (no monolith regrowth)
  C. no source CRUD inside the News feature
  D. 3-pane DOM structure + sentiment panel removal
  E. selection/refresh contract markers
  F. safe rendering + external links
  G. responsive structural CSS
  H. app.js gains no News logic; no new EventSource/timers
  I. explicit N-UI1 exclusions (deep links, read/unread, full-text, iframe)

Live layout/interaction is verified by browser E2E (see coding report);
this file guards structure + contracts in CI without a browser.
"""
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R

_UI = os.path.join(_PROJECT_DIR, "web", "ui")
_JS = os.path.join(_UI, "js")
_NF = os.path.join(_JS, "features", "news")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _feat(name):
    return _read(os.path.join(_NF, name))


# ===================================================================
# A. Feature module tree + ownership
# ===================================================================

def test_feature_tree(r: R) -> None:
    for name in ("index.js", "state.js", "feeds.js",
                 "article-list.js", "reader.js", "sentiment.js"):
        try:
            src = _feat(name)
        except Exception as exc:
            r.fail(f"NUI1:readable:{name}", str(exc))
            return
        if "export " in src:
            r.ok(f"NUI1:readable:{name}")
        else:
            r.fail(f"NUI1:readable:{name}", "no exports")
    # state.js is the ONLY shared-state mutation owner.
    state = _feat("state.js")
    if "createNewsStore" in state and "selectedItemId" in state:
        r.ok("NUI1:state_owner")
    else:
        r.fail("NUI1:state_owner", "store shape missing")
    for pane, name in (( "feeds.js", "feeds"), ("article-list.js", "list"),
                       ("reader.js", "reader")):
        src = _feat(pane)
        if "store.on(" in src:
            r.ok(f"NUI1:subscribes:{name}")
        else:
            r.fail(f"NUI1:subscribes:{name}", "pane never subscribes")
        # Panes may call select()/setFilters(); they must not wholesale
        # replace shared collections (that is setArticles' job).
        if "store.articles =" in src or "store.order =" in src:
            r.fail(f"NUI1:no_direct_mutation:{name}", "pane replaces shared state")
        else:
            r.ok(f"NUI1:no_direct_mutation:{name}")
    # No mutable window globals in the feature.
    for name in ("index.js", "state.js", "feeds.js",
                 "article-list.js", "reader.js", "sentiment.js"):
        if "window." in _feat(name):
            r.fail(f"NUI1:no_window:{name}", "window global used")
        else:
            r.ok(f"NUI1:no_window:{name}")


# ===================================================================
# B. news.js thin compat
# ===================================================================

def test_news_shim(r: R) -> None:
    src = _read(os.path.join(_JS, "news.js"))
    if "export function initNewsUI" in src and "export async function openNews" in src:
        r.ok("NUI1:shim_api")
    else:
        r.fail("NUI1:shim_api", "compat surface changed")
    if "features/news/index.js" in src:
        r.ok("NUI1:shim_delegates")
    else:
        r.fail("NUI1:shim_delegates", "not delegating to feature")
    lines = len(src.splitlines())
    if lines <= 40:
        r.ok(f"NUI1:shim_thin:{lines}")
    else:
        r.fail(f"NUI1:shim_thin:{lines}", "shim regrowing logic")
    for banned in ("innerHTML", "fetch(", "/api/news", "querySelectorAll"):
        if banned in src:
            r.fail(f"NUI1:shim_clean:{banned}", "logic leaked into shim")
        else:
            r.ok(f"NUI1:shim_clean:{banned}")


# ===================================================================
# C. No source CRUD inside the News feature
# ===================================================================

def test_no_crud(r: R) -> None:
    blob = "\n".join(_feat(n) for n in ("index.js", "state.js", "feeds.js",
                                        "article-list.js", "reader.js",
                                        "sentiment.js"))
    for banned in ("news-sources-body", "news-source-modal", "news-src-id",
                   "/api/news/sources", "upsert_source", "data-news-action"):
        if banned in blob:
            r.fail(f"NUI1:no_crud:{banned}", "CRUD leaked into reader")
        else:
            r.ok(f"NUI1:no_crud:{banned}")
    # Manage Sources stays a plain link to Settings.
    html = _read(os.path.join(_UI, "index.html"))
    if 'href="#/settings/news-sources"' in html:
        r.ok("NUI1:manage_link")
    else:
        r.fail("NUI1:manage_link", "Manage Sources link missing")


# ===================================================================
# D. 3-pane DOM + sentiment panel removal
# ===================================================================

def test_dom(r: R) -> None:
    html = _read(os.path.join(_UI, "index.html"))
    start = html.find('id="view-news"')
    end = html.find("LOGS VIEW", start)
    news = html[start:end if end > 0 else len(html)]
    for kept in ("news-shell", "news-strip", "news-cols", "news-feeds",
                 "news-source-list", "news-articles-list", "news-reader",
                 "news-agg", "news-new-pill"):
        if kept in news:
            r.ok(f"NUI1:dom:{kept}")
        else:
            r.fail(f"NUI1:dom:{kept}", "missing from News view")
    # Phone Back button is rendered by reader.js (not static HTML).
    if "news-reader-back" in _feat("reader.js"):
        r.ok("NUI1:dom:news-reader-back")
    else:
        r.fail("NUI1:dom:news-reader-back", "missing from reader")
    for gone in ("news-sentiment-btn", "news-sentiment-result",
                 "news-article-title", "news-toolbar"):
        if gone not in news:
            r.ok(f"NUI1:removed:{gone}")
        else:
            r.fail(f"NUI1:removed:{gone}", "old stacked UI remains")


# ===================================================================
# E. Selection / refresh contract markers
# ===================================================================

def test_selection_contract(r: R) -> None:
    state = _feat("state.js")
    for marker in ("selectedItemId", "resetSelection", "prevOrder",
                   "setArticles", "countsBySource"):
        if marker in state:
            r.ok(f"NUI1:state:{marker}")
        else:
            r.fail(f"NUI1:state:{marker}", "missing")
    idx = _feat("index.js")
    for marker in ("news-new-pill", "refreshing", "loadSentiment",
                   "/api/news/refresh", "loadedOnce"):
        if marker in idx:
            r.ok(f"NUI1:orch:{marker}")
        else:
            r.fail(f"NUI1:orch:{marker}", "missing")
    lst = _feat("article-list.js")
    if "scrollTop" in lst and "aria-selected" in lst:
        r.ok("NUI1:list_scroll_selection")
    else:
        r.fail("NUI1:list_scroll_selection", "scroll/active handling missing")
    reader = _feat("reader.js")
    if "scrollTop" in reader and "lastRenderedId" in reader:
        r.ok("NUI1:reader_scroll")
    else:
        r.fail("NUI1:reader_scroll", "reader scroll ownership missing")
    # Rail/compact source controls: "" means All Sources and must never
    # fall back to a stale store value (live-found: All-click restuck ET).
    feeds = _feat("feeds.js")
    if "overrideSourceId" in feeds:
        r.ok("NUI1:rail_authoritative")
    else:
        r.fail("NUI1:rail_authoritative", "rail sid not authoritative")
    if "compactSel.value) || store.filters" in feeds:
        r.fail("NUI1:no_stale_source", "falsy-All falls back to stale source")
    else:
        r.ok("NUI1:no_stale_source")


# ===================================================================
# F. Safe rendering + external links
# ===================================================================

def test_safe_render(r: R) -> None:
    reader = _feat("reader.js")
    if "textContent" in reader:
        r.ok("NUI1:summary_text")
    else:
        r.fail("NUI1:summary_text", "summary not rendered as text")
    if 'target="_blank"' in reader and 'rel="noopener"' in reader:
        r.ok("NUI1:safe_link")
    else:
        r.fail("NUI1:safe_link", "Open Original lacks safe attrs")
    for name in ("article-list.js", "reader.js", "feeds.js"):
        if "onclick=" in _feat(name):
            r.fail(f"NUI1:no_inline:{name}", "inline handler")
        else:
            r.ok(f"NUI1:no_inline:{name}")


# ===================================================================
# G. Responsive structural CSS
# ===================================================================

def test_responsive_css(r: R) -> None:
    css = _read(os.path.join(_UI, "css", "style.css"))
    for marker in ("--news-rail-w", "grid-template-columns: var(--news-rail-w)",
                   "@media (max-width: 1100px)", "@media (max-width: 760px)",
                   "news-mobile-reader", ".news-reader-back",
                   ".news-compact", "overflow-y: auto"):
        if marker in css:
            r.ok(f"NUI1:css:{marker[:30]}")
        else:
            r.fail(f"NUI1:css:{marker[:30]}", "missing")


# ===================================================================
# H. app.js / runtime budget
# ===================================================================

def test_runtime_budget(r: R) -> None:
    app = _read(os.path.join(_JS, "app.js"))
    if 'from "./news.js"' in app:
        r.ok("NUI1:app_import")
    else:
        r.fail("NUI1:app_import", "app.js import changed")
    for banned in ("selectedItemId", "news-reader", "news-source-list",
                   "features/news"):
        if banned in app:
            r.fail(f"NUI1:app_clean:{banned}", "News logic in app.js")
        else:
            r.ok(f"NUI1:app_clean:{banned}")
    blob = "\n".join(_feat(n) for n in ("index.js", "state.js", "feeds.js",
                                        "article-list.js", "reader.js",
                                        "sentiment.js"))
    for banned in ("new EventSource", "setInterval"):
        if banned in blob:
            r.fail(f"NUI1:budget:{banned}", "runtime ownership changed")
        else:
            r.ok(f"NUI1:budget:{banned}")
    # Timers: exactly filter-debounce (feeds.js) + transient error
    # dismissal (index.js). Anything else is timer creep.
    uses = blob.count("setTimeout(")
    if uses == 2:
        r.ok(f"NUI1:budget:setTimeout:{uses}")
    else:
        r.fail(f"NUI1:budget:setTimeout:{uses}", "timer creep")


# ===================================================================
# I. N-UI1 exclusions
# ===================================================================

def test_exclusions(r: R) -> None:
    blob = "\n".join(_feat(n) for n in ("index.js", "state.js", "feeds.js",
                                        "article-list.js", "reader.js",
                                        "sentiment.js"))
    html = _read(os.path.join(_UI, "index.html"))
    for banned in ("#/news/read", "readability", "Readability",
                   "<iframe", "mark-as-read", "read_at", "is_read",
                   "/api/mcp", "toggle-status"):
        if banned in blob or banned in html:
            r.fail(f"NUI1:excluded:{banned}", "out-of-scope feature present")
        else:
            r.ok(f"NUI1:excluded:{banned}")


# ===================================================================
# MAIN
# ===================================================================

def main() -> None:
    r = R()
    print("=" * 60)
    print("N-UI1 3-column News reader regression tests")
    print("=" * 60)

    print("\n--- A Feature tree ---")
    test_feature_tree(r)

    print("\n--- B news.js shim ---")
    test_news_shim(r)

    print("\n--- C No CRUD ---")
    test_no_crud(r)

    print("\n--- D DOM ---")
    test_dom(r)

    print("\n--- E Selection contract ---")
    test_selection_contract(r)

    print("\n--- F Safe render ---")
    test_safe_render(r)

    print("\n--- G Responsive CSS ---")
    test_responsive_css(r)

    print("\n--- H Runtime budget ---")
    test_runtime_budget(r)

    print("\n--- I Exclusions ---")
    test_exclusions(r)

    print("\n" + "=" * 60)
    r.summary()
    print("=" * 60)
    if r.failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
