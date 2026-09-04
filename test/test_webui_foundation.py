"""Focused tests for the MarketHub WebUI foundation build.

These are intentionally dependency-free (stdlib only) so they run without the
`helpers.runner` harness used by the pre-existing (and currently broken)
test_news_reader.py / test_settings_ux.py.

They assert the structural/contract guarantees called out in the
IMPLEMENTATION spec:

  * CSS foundation files exist with correct ownership
  * themeable tokens only (no literal colors outside token/theme blocks)
  * real index.html shell refactored (.app / .app-topbar / .app-ticker /
    .app-main / .app-footer) and News ported to the generic splitter
  * generic splitter JS is News-agnostic and has no persistence/polling
  * News JS no longer depends on WebAwesome components
"""

from __future__ import annotations

import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "web" / "ui"
CSS = UI / "css"
JS = UI / "js"

HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
WA_TAG = re.compile(r"wa-(split-panel|select|input|button|option|tag|badge|skeleton|icon|relative-time|callout)\b")


def test_foundation_files_exist():
    for f in ("tokens.css", "base.css", "shell.css", "components.css",
              "features/news.css", "app.css"):
        assert (CSS / f).is_file(), f"missing foundation file: {f}"


def test_app_css_is_entry_orchestrator():
    text = (CSS / "app.css").read_text(encoding="utf-8")
    assert "@import" in text, "app.css must orchestrate imports"
    for dep in ("tokens.css", "base.css", "shell.css", "components.css", "features/news.css"):
        assert dep in text, f"app.css must import {dep}"


def test_tokens_define_semantic_variables_and_themes():
    text = (CSS / "tokens.css").read_text(encoding="utf-8")
    for tok in ("--bg", "--surface-1", "--text", "--accent", "--border",
                "--success", "--danger", "--focus-ring"):
        assert tok in text, f"tokens.css missing {tok}"
    assert '[data-theme="dark"]' in text, "dark theme token block missing"
    assert '[data-theme="light"]' in text, "light reference theme missing"


def test_no_literal_colors_outside_tokens():
    # components/shell/news/base must consume semantic variables only.
    for f in ("shell.css", "components.css", "features/news.css", "base.css"):
        body = (CSS / f).read_text(encoding="utf-8")
        # strip /* */ comments to avoid false positives
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        assert not HEX.search(body), f"literal color found in {f}: {HEX.search(body).group(0)}"


def test_shell_css_owns_shell_and_splitter():
    text = (CSS / "shell.css").read_text(encoding="utf-8")
    for sel in (".app", ".app-main", ".app-footer", ".split", ".split-gutter",
                ".pane", ".workspace", ".page"):
        assert sel in text, f"shell.css missing {sel}"
    # topbar may be expressed as .app-topbar (and/or legacy .topnav alias)
    assert ".app-topbar" in text or ".topnav" in text


def test_index_html_uses_new_shell_and_not_wa_split():
    html = (UI / "index.html").read_text(encoding="utf-8")
    assert '<div class="app">' in html, "index.html must wrap content in .app"
    assert "app-topbar" in html or "topnav" in html
    assert "app-ticker" in html or "ticker-strip" in html
    assert 'class="app-main"' in html
    assert "app-footer" in html
    # News must not depend on wa-split-panel
    assert "wa-split-panel" not in html, "News still uses wa-split-panel"
    # foundation linked
    assert "app.css" in html


def test_news_markup_uses_generic_splitter_and_ui_controls():
    html = (UI / "index.html").read_text(encoding="utf-8")
    # isolate the News view
    news = html[html.index('id="view-news"'): html.index('id="view-logs"')]
    assert "split news-split" in news or "class=\"split" in news
    assert "split-gutter" in news
    assert 'data-split' in news
    # ui-* controls instead of wa-* in the toolbar
    assert "ui-select" in news and "ui-input" in news and "ui-btn" in news
    assert not WA_TAG.search(news), "News markup still references a wa-* component"


def test_style_css_no_longer_owns_shell():
    text = (CSS / "style.css").read_text(encoding="utf-8")
    assert "wa-split-panel" not in text, "legacy style.css still has wa-split-panel rules"
    # The old fixed-position shell classes must be gone (drawer/modal may
    # legitimately keep position:fixed as overlays).
    assert "topnav" not in text, "legacy .topnav shell rule still in style.css"
    assert "ticker-strip" not in text, "legacy .ticker-strip shell rule still in style.css"
    # main margin-top shell compensation removed
    assert "margin-top: calc(var(--nav-h)" not in text


def test_splitter_is_generic_and_safe():
    text = (JS / "core" / "splitter.js").read_text(encoding="utf-8")
    assert "export function initSplitters" in text
    assert "setPointerCapture" in text
    assert "pointermove" in text
    assert "keydown" in text
    # No forbidden mechanisms (allow the words only inside docstrings)
    assert re.search(r"localStorage[\s.\(]", text) is None, "splitter must not persist"
    assert "setInterval" not in text, "splitter must not poll"
    assert "wa-" not in text, "splitter must be News-agnostic"
    assert "news" not in text.lower(), "splitter must not know about News"


def test_news_js_no_webawesome_components():
    for f in ("filters.js", "article-list.js", "reader.js"):
        text = (JS / "features" / "news" / f).read_text(encoding="utf-8")
        assert not WA_TAG.search(text), f"{f} still references a wa-* component"


def test_sentiment_score_class_is_uibadge_modifier():
    text = (JS / "features" / "news" / "sentiment.js").read_text(encoding="utf-8")
    assert '"bull"' in text and '"bear"' in text and '"neutral"' in text
    assert "success" not in text and "danger" not in text, \
        "scoreClass should map to ui-badge modifiers, not WA variants"


def test_app_js_sets_data_theme_and_wires_splitter():
    text = (JS / "app.js").read_text(encoding="utf-8")
    assert 'setAttribute("data-theme"' in text, "theme toggle must set data-theme"
    assert "initSplitters" in text, "app.js must initialize the splitter"
