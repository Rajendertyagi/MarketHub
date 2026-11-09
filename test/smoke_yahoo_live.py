#!/usr/bin/env python3
"""Controlled live smoke test against real Yahoo Finance via yfinance.

Read-only. Bounded. Not part of the normal test suite.
Run: python test/smoke_yahoo_live.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from yfinance_service.service import YahooFinanceService

RESULTS: list[tuple[str, str, str, str]] = []  # category, symbol, op, verdict


def record(cat: str, sym: str, op: str, verdict: str, detail: str = "") -> None:
    RESULTS.append((cat, sym, op, verdict))
    mark = {"LIVE VERIFIED": "OK  ", "UNAVAILABLE": "N/A ", "ERROR": "FAIL",
            "LIMITED": "LIM "}.get(verdict, "?   ")
    print(f"[{mark}] {cat:<12} {sym:<14} {op:<18} {detail[:90]}")


def brief(payload: dict) -> str:
    if payload.get("status") == "error":
        return f"{payload.get('code')}: {payload.get('reason', '')[:60]}"
    d = payload.get("data", {})
    bits = []
    for k in ("price", "count", "call_count"):
        if k in d:
            bits.append(f"{k}={d[k]}")
    return f"freshness={payload.get('freshness')} {' '.join(bits)}"


async def main() -> int:
    import yfinance
    svc = YahooFinanceService(yf=yfinance)
    print(f"yfinance version: {yfinance.__version__}\n")

    symbols = ["RELIANCE.NS", "TCS.NS", "^NSEI", "^NSEBANK", "AAPL", "MSFT"]

    for sym in symbols:
        # quote
        try:
            r = await svc.quote(symbol=sym)
            if r.get("status") == "ok":
                p = r["data"].get("price")
                ok = p is not None
                record("quote", sym, "yahoo_quote",
                       "LIVE VERIFIED" if ok else "UNAVAILABLE", brief(r))
            else:
                record("quote", sym, "yahoo_quote", "UNAVAILABLE", brief(r))
        except Exception as exc:
            record("quote", sym, "yahoo_quote", "ERROR", str(exc)[:80])

    # history
    for sym in ["RELIANCE.NS", "^NSEI", "AAPL"]:
        try:
            r = await svc.history(symbol=sym, period="5d", interval="1d")
            n = r.get("data", {}).get("count", 0)
            record("history", sym, "yahoo_history",
                   "LIVE VERIFIED" if n else "UNAVAILABLE", brief(r))
        except Exception as exc:
            record("history", sym, "yahoo_history", "ERROR", str(exc)[:80])

    # company
    for sym in ["RELIANCE.NS", "AAPL"]:
        try:
            r = await svc.company(symbol=sym)
            n = len(r.get("data", {}))
            record("company", sym, "yahoo_company",
                   "LIVE VERIFIED" if n else "UNAVAILABLE", brief(r))
        except Exception as exc:
            record("company", sym, "yahoo_company", "ERROR", str(exc)[:80])

    # financials
    for sym in ["AAPL", "RELIANCE.NS"]:
        try:
            r = await svc.financials(symbol=sym, period="quarterly",
                                     statement="income")
            st = r.get("data", {}).get("statements", {}).get("income", {})
            n = st.get("count", 0)
            record("financials", sym, "yahoo_financials",
                   "LIVE VERIFIED" if n else "UNAVAILABLE", brief(r))
        except Exception as exc:
            record("financials", sym, "yahoo_financials", "ERROR", str(exc)[:80])

    # corporate actions
    for sym in ["AAPL", "RELIANCE.NS"]:
        try:
            r = await svc.corporate_actions(symbol=sym)
            d = r.get("data", {})
            n = d.get("dividend_count", 0) + d.get("split_count", 0)
            record("actions", sym, "yahoo_corp_actions",
                   "LIVE VERIFIED" if n else "UNAVAILABLE", brief(r))
        except Exception as exc:
            record("actions", sym, "yahoo_corp_actions", "ERROR", str(exc)[:80])

    # holders
    try:
        r = await svc.holders(symbol="AAPL", holder_type="institutional")
        n = r.get("data", {}).get("groups", {}).get("institutional", {}).get(
            "count", 0)
        record("holders", "AAPL", "yahoo_holders",
               "LIVE VERIFIED" if n else "UNAVAILABLE", brief(r))
    except Exception as exc:
        record("holders", "AAPL", "yahoo_holders", "ERROR", str(exc)[:80])

    # options (US only on Yahoo)
    for sym in ["AAPL", "RELIANCE.NS"]:
        try:
            r = await svc.options(symbol=sym)
            n = r.get("data", {}).get("call_count", 0)
            record("options", sym, "yahoo_options",
                   "LIVE VERIFIED" if n else "UNAVAILABLE", brief(r))
        except Exception as exc:
            err = ""
            try:
                err = exc.code
            except Exception:
                err = str(exc)[:40]
            record("options", sym, "yahoo_options", "UNAVAILABLE", err)

    # search
    for q in ["Reliance Industries", "bitcoin", "gold", "TCS"]:
        try:
            r = await svc.search(query=q, limit=5)
            n = r.get("data", {}).get("count", 0)
            top = r.get("data", {}).get("matches", [{}])[0].get("symbol")
            record("search", q[:12], "yahoo_search",
                   "LIVE VERIFIED" if n else "UNAVAILABLE", f"n={n} top={top}")
        except Exception as exc:
            record("search", q[:12], "yahoo_search", "ERROR", str(exc)[:80])

    # cross-asset-class quotes
    for sym, label in [("BTC-USD", "crypto"), ("GC=F", "commodity"),
                       ("EURUSD=X", "forex"), ("SPY", "etf")]:
        try:
            r = await svc.quote(symbol=sym)
            p = r.get("data", {}).get("price")
            record(label, sym, "yahoo_quote",
                   "LIVE VERIFIED" if p else "UNAVAILABLE",
                   f"price={p} type={r.get('quote_type')}")
        except Exception as exc:
            record(label, sym, "yahoo_quote", "ERROR", str(exc)[:80])

    # live websocket (may legitimately be unavailable)
    for sym in ["AAPL", "^NSEI"]:
        try:
            r = await svc.live_quote(symbol=sym, timeout=8.0)
            if r.get("status") == "ok":
                record("stream", sym, "yahoo_live_quote", "LIVE VERIFIED",
                       f"price={r['data'].get('price')}")
            else:
                record("stream", sym, "yahoo_live_quote", "UNAVAILABLE",
                       f"{r.get('code')}")
        except Exception as exc:
            record("stream", sym, "yahoo_live_quote", "ERROR", str(exc)[:80])

    # summary
    print("\n" + "=" * 70)
    tally: dict[str, int] = {}
    for _, _, _, v in RESULTS:
        tally[v] = tally.get(v, 0) + 1
    for k, v in sorted(tally.items()):
        print(f"  {k}: {v}")
    json.dump([{"category": c, "symbol": s, "op": o, "verdict": v}
               for c, s, o, v in RESULTS],
              open(os.path.join(os.path.dirname(__file__),
                                "yahoo_live_results.json"), "w"), indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
