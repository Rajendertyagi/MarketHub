#!/usr/bin/env python3
"""Manual upstream-version check for the X search outage watch.

Read-only: queries the PyPI JSON API for twitter-cli and
xclienttransaction and compares against the pins recorded in
docs/X_UPSTREAM_WATCH.md. Changes nothing. Not collected by pytest
(name does not start with test_).

Exit 0: pins still current (no action).
Exit 2: a newer release exists (run the watch checklist).
Exit 1: check itself failed (network/parse error).
"""

from __future__ import annotations

import json
import sys
import urllib.request

PINS = {
    "twitter-cli": "0.8.5",
    "xclienttransaction": "1.0.3",
}


def _latest(name: str) -> str | None:
    try:
        with urllib.request.urlopen(
            f"https://pypi.org/pypi/{name}/json", timeout=20,
        ) as resp:
            payload = json.loads(resp.read())
        return str(payload["info"]["version"])
    except Exception as exc:
        print(f"check failed for {name}: {type(exc).__name__}: {exc}")
        return None


def main() -> int:
    newer: list[str] = []
    failed = False
    for name, pinned in PINS.items():
        latest = _latest(name)
        if latest is None:
            failed = True
            continue
        flag = "NEWER — run the watch checklist" if latest != pinned else "current"
        print(f"{name}: pinned={pinned} latest={latest} -> {flag}")
        if latest != pinned:
            newer.append(f"{name} {pinned} -> {latest}")
    if failed and not newer:
        return 1
    if newer:
        print("UPDATES AVAILABLE: " + "; ".join(newer))
        return 2
    print("No upstream updates. No action.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
