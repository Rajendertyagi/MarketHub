"""Chat acceptance tests (CH) — Packages 33/35/36/42/48.

Deterministic: FakeChatAgent emits known tool calls; the REAL tool
registry executes against synthetic catalog + market state. No external
AI provider, no network.
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R


def _mk_world():
    """Synthetic catalog + market state + alert store + registry."""
    from core.persistence.store import EventStore
    from app.instruments import InstrumentCatalog
    from app.market_intel import MarketIntel
    from app.chat_tools import ChatToolRegistry

    tmp = tempfile.mkdtemp()
    store = EventStore(os.path.join(tmp, "e.db"))
    rows = []
    rows.append(dict(provider="fyers", instrument_token="100IDX",
                     exchange="NSE", tradingsymbol="NSE:NIFTY50-INDEX",
                     name="Nifty 50", instrument_type="INDEX",
                     segment="10", underlying="NIFTY"))
    rows.append(dict(provider="fyers", instrument_token="200EQ",
                     exchange="NSE", tradingsymbol="NSE:RELIANCE-EQ",
                     name="Reliance Industries", instrument_type="EQUITY",
                     segment="10", underlying="RELIANCE"))
    for s in (23900.0, 23950.0, 24000.0):
        for ot in ("CE", "PE"):
            rows.append(dict(
                provider="fyers", instrument_token=f"400{s}{ot}",
                exchange="NSE", tradingsymbol=f"NSE:NIFTY{s}{ot}",
                name="NIFTY OPT", instrument_type="OPTION", segment="11",
                underlying="NIFTY", expiry="2026-09-01", strike=s,
                option_type=ot, lot_size=75))
    store.replace_provider_instruments("fyers", rows)
    catalog = InstrumentCatalog(store)

    from market.service import MarketService, QuotePatch
    msvc = MarketService()

    def intel_spot(exchange, token):
        return msvc.get_quote_now(exchange, token)

    intel = MarketIntel(catalog, spot_provider=intel_spot)

    async def seed():
        await msvc.apply_quote(QuotePatch(
            exchange="NSE", instrument_token="100IDX",
            tradingsymbol="NSE:NIFTY50-INDEX",
            received_ts=datetime.now(timezone.utc),
            reported_fields={"ltp": 24005.5}))
        await msvc.apply_quote(QuotePatch(
            exchange="NSE", instrument_token="200EQ",
            tradingsymbol="NSE:RELIANCE-EQ",
            received_ts=datetime.now(timezone.utc),
            reported_fields={"ltp": 1420.0}))

    asyncio.run(seed())

    registry = ChatToolRegistry(market_intel=intel,
                                market_service=msvc, store=store)
    return registry, msvc, store


async def _collect(agent):
    events = []
    async for ev in agent.run([{"role": "user", "content": "test"}]):
        events.append(ev)
    return events


def test_ch1_market_questions(runner: R) -> None:
    """'What's NIFTY doing?' path: search -> quote with freshness."""
    registry, _msvc, _store = _mk_world()
    from app.chat_agent import FakeChatAgent

    agent = FakeChatAgent(script=[
        {"tool": "market_quote", "arguments": {"query": "NIFTY"}},
        {"say": "NIFTY is at 24005.5 (live)."},
    ], tool_registry=registry)

    events = asyncio.run(_collect(agent))
    tool_results = [e for e in events if e["type"] == "tool_result"]
    runner.assert_eq("CH1-one-tool", len(tool_results), 1)
    result = tool_results[0]["result"]
    runner.assert_eq("CH1-resolved-symbol",
                     result["instrument"]["symbol"], "NSE:NIFTY50-INDEX")
    runner.assert_eq("CH1-ltp", result["quote"]["ltp"], 24005.5)
    runner.assert_false("CH1-freshness-honest",
                        result["freshness"]["stale"])
    deltas = "".join(e.get("text", "") for e in events if e["type"] == "delta")
    runner.assert_in("CH1-answer", "24005.5", deltas)


def test_ch2_option_chain_question(runner: R) -> None:
    registry, _msvc, _store = _mk_world()
    from app.chat_agent import FakeChatAgent

    agent = FakeChatAgent(script=[
        {"tool": "option_chain",
         "arguments": {"underlying": "NIFTY", "window": 1}},
        {"say": "ATM is 23950."},
    ], tool_registry=registry)

    events = asyncio.run(_collect(agent))
    result = next(e for e in events if e["type"] == "tool_result")["result"]
    # Live spot 24005.5 (seeded quote) -> ATM = nearest listed strike.
    runner.assert_eq("CH2-atm", result["atm_strike"], 24000.0)
    runner.assert_eq("CH2-spot-basis", result["spot_basis"], "live")
    runner.assert_true("CH2-ce-pe-paired",
                       all(r["call"] and r["put"] for r in result["rows"]))
    runner.assert_eq("CH2-window-rows", result["strikes_loaded"], 2)


def test_ch3_alert_actions(runner: R) -> None:
    registry, _msvc, store = _mk_world()
    from app.chat_agent import FakeChatAgent

    # Create via human query.
    agent = FakeChatAgent(script=[
        {"tool": "market_alert_create",
         "arguments": {"instrument_query": "RELIANCE",
                       "operator": "lt", "threshold": 1400}},
        {"say": "Alert created."},
    ], tool_registry=registry)
    events = asyncio.run(_collect(agent))
    created = next(e for e in events if e["type"] == "tool_result")
    runner.assert_eq("CH3-create-status", created["result"]["status"],
                     "created")
    alert_id = created["result"]["alert"]["id"]

    # Persistence confirmed.
    alerts = store.list_alerts()
    runner.assert_eq("CH3-persisted", len(alerts), 1)
    runner.assert_eq("CH3-persisted-symbol",
                     alerts[0]["tradingsymbol"], "NSE:RELIANCE-EQ")
    runner.assert_eq("CH3-persisted-op", alerts[0]["operator"], "lt")

    # List.
    agent2 = FakeChatAgent(script=[
        {"tool": "market_alert_list", "arguments": {}}],
        tool_registry=registry)
    events2 = asyncio.run(_collect(agent2))
    listed = next(e for e in events2 if e["type"] == "tool_result")
    runner.assert_eq("CH3-list-count", listed["result"]["count"], 1)

    # Disable then delete.
    agent3 = FakeChatAgent(script=[
        {"tool": "market_alert_disable", "arguments": {"alert_id": alert_id}},
        {"tool": "market_alert_delete", "arguments": {"alert_id": alert_id}},
    ], tool_registry=registry)
    asyncio.run(_collect(agent3))
    runner.assert_eq("CH3-deleted", len(store.list_alerts()), 0)


def test_ch4_no_hallucinated_data(runner: R) -> None:
    """Unknown symbol must produce an error event, not invented data."""
    registry, _msvc, _store = _mk_world()
    from app.chat_agent import FakeChatAgent

    agent = FakeChatAgent(script=[
        {"tool": "market_quote", "arguments": {"query": "ZXYZZY"}},
    ], tool_registry=registry)
    events = asyncio.run(_collect(agent))
    result = next(e for e in events if e["type"] == "tool_result")["result"]
    runner.assert_in("CH4-error-not-fabricated", "no instrument matches",
                     str(result))


def test_ch5_registry_boundaries(runner: R) -> None:
    """No trading tools may exist in the chat registry. Ever."""
    registry, _msvc, _store = _mk_world()
    forbidden = ("order", "trade", "buy", "sell", "place", "cancel_order",
                 "fund", "transfer")
    for d in registry.definitions:
        name = d["function"]["name"].lower()
        desc = d["function"]["description"].lower()
        for word in forbidden:
            runner.assert_false(f"CH5-no-trading-tool:{word}:{name}",
                                word in name)


if __name__ == "__main__":
    runner = R()
    test_ch1_market_questions(runner)
    test_ch2_option_chain_question(runner)
    test_ch3_alert_actions(runner)
    test_ch4_no_hallucinated_data(runner)
    test_ch5_registry_boundaries(runner)
    sys.exit(0 if runner.summary() else 1)
