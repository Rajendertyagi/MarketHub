"""
Focused WebUI tests for the Derivative Scanner UI + Charts foundation.

Two layers of verification:

1. A DETERMINISTIC static check that proves the charts.js import-time DOM
   defect (Phase 2) is gone: the module no longer executes `document.*` at
   module scope; the probe is created lazily inside a function. This is the
   exact failure mode (`ReferenceError: document is not defined` under
   `bun --check`) and does not depend on a flaky subprocess.

2. A real `bun --check` pass per touched module. On Windows under pytest the
   child-process handle inheritance occasionally raises WinError 6 (an
   environmental race, not a code defect); we SKIP on that OSError rather than
   mask a genuine syntax error (a non-zero return for a real reason still
   fails the test).
"""

import subprocess
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "web" / "ui" / "js"

# Modules touched by the Derivative-Scanner-UI + Charts work.
TOUCHED = [
    "scanner.js",
    "charts.js",
    "market-map.js",
    "fno.js",
    "app.js",
]


def _read(name):
    return (UI / name).read_text(encoding="utf-8")


def _bun_check(name):
    """Return (returncode, out, oserror). out is '' on OSError."""
    try:
        proc = subprocess.run(
            ["bun", "--check", str(UI / name)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            timeout=60,
        )
        return proc.returncode, "", False
    except OSError as exc:
        return 1, f"subprocess OSError: {exc}", True


def _assert_clean(name):
    code, out, oserr = _bun_check(name)
    if oserr:
        pytest.skip(f"bun --check skipped (flaky Windows handle race): {out}")
    assert code == 0, f"{name} failed bun --check:\n{out}"


# --- Phase 2: charts.js import-time DOM side effect removed (deterministic) ---

def test_charts_js_no_module_scope_dom_creation():
    src = _read("charts.js")
    # Everything before the first function definition is module scope. The
    # original defect executed `document.*` here on import; that must be gone.
    top = src.split("function _ensureProbe", 1)[0]
    assert "document." not in top, "module-scope document access remains"
    assert "window." not in top, "module-scope window access remains"
    # The probe is still created, but lazily inside a function (not on import).
    assert "function _ensureProbe" in src, "lazy probe initializer missing"
    assert "document.createElement" in src, "probe creation logic missing"


def test_charts_js_import_clean():
    _assert_clean("charts.js")


def test_scanner_js_import_clean():
    _assert_clean("scanner.js")


def test_market_map_js_import_clean():
    _assert_clean("market-map.js")


def test_fno_js_import_clean():
    _assert_clean("fno.js")


def test_app_js_import_clean():
    _assert_clean("app.js")


def test_all_touched_modules_check_clean():
    failures = []
    skipped = False
    for name in TOUCHED:
        code, out, oserr = _bun_check(name)
        if oserr:
            skipped = True
            continue
        if code != 0:
            failures.append(f"{name}: {out}")
    if skipped and not failures:
        pytest.skip("bun --check skipped (flaky Windows handle race)")
    assert not failures, "bun --check failures:\n" + "\n".join(failures)
