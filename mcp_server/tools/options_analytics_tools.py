"""Options analytics MCP tools: derived F&O analytics over MarketHub's chain.

These wrap ``market.analytics`` (pure functions over the canonical
``OptionChainSnapshot``) and the unified ``MarketIntel`` service, so an AI client
gets the same derived analytics TBMCP exposed — computed locally and
provider-agnostically, with no extra broker API calls beyond the single option-
chain fetch.

All reads go through the shared services; no broker connections are created here.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp_server.contract import (
    TOOL_ANALYZE_OPTION_CHAIN,
    TOOL_COMPUTE_ATM,
    TOOL_COMPUTE_FUTURES_BASIS,
    TOOL_COMPUTE_GEX,
    TOOL_COMPUTE_IV_SKEW,
    TOOL_COMPUTE_MAX_PAIN,
    TOOL_COMPUTE_OI_BUILDUP,
    TOOL_COMPUTE_PCR,
    TOOL_COMPUTE_STRADDLE,
    TOOL_COMPUTE_SUPPORT_RESISTANCE,
    TOOL_COMPUTE_TOP_OI_STRIKES,
    TOOL_PRICE_BEAR_PUT_SPREAD,
    TOOL_PRICE_BULL_CALL_SPREAD,
    TOOL_PRICE_IRON_CONDOR,
    TOOL_PRICE_LONG_BUTTERFLY,
    TOOL_PRICE_LONG_STRADDLE,
    TOOL_PRICE_LONG_STRANGLE,
)

from market.analytics.option_chain import (
    analyze_option_chain,
    compute_atm,
    compute_futures_basis,
    compute_gex,
    compute_iv_skew,
    compute_max_pain,
    compute_oi_buildup,
    compute_pcr,
    compute_straddle,
    compute_support_resistance,
    compute_top_oi_strikes,
)
from market.analytics.strategies import (
    price_bear_put_spread,
    price_bull_call_spread,
    price_iron_condor,
    price_long_butterfly,
    price_long_straddle,
    price_long_strangle,
)


def register_options_analytics_tools(mcp, services, **kwargs) -> None:
    """Register derived options-analytics tools."""
    intel = getattr(services, "market_intel", None)
    md = getattr(services, "provider_market_data", None)
    mkt = getattr(services, "market_service", None)

    async def _snapshot_for(underlying: str, expiry: str | None):
        """Resolve a symbol to an OptionChainSnapshot (auto-picking expiry)."""
        if intel is None or md is None:
            return None, {"error": "market intelligence / data service unavailable"}
        row = intel.resolve_underlying(underlying)
        if row is None:
            return None, {"error": f"unknown underlying '{underlying}'"}
        exchange = row.get("exchange")
        token = row.get("instrument_token")
        tradingsymbol = row.get("tradingsymbol", "")
        und_key = row.get("underlying") or tradingsymbol

        chosen = expiry
        if not chosen:
            exp = await asyncio.to_thread(intel.option_expiries, underlying)
            exps = exp.get("expiries") or []
            if not exps:
                return None, {"error": f"no listed options for '{und_key}'"}
            chosen = exps[0]

        snap = await md.option_chain(
            instrument_key=token, exchange=exchange,
            tradingsymbol=tradingsymbol, expiry=chosen)
        return snap, None

    def _embed_chain(snap, max_strikes: int | None = None) -> dict[str, Any]:
        """Compact, readable chain view for embedding in analyze_option_chain."""
        rows = []
        for r in snap.strikes:
            entry: dict[str, Any] = {"strike": r.strike, "atm": r.atm}
            if r.call is not None:
                entry["call"] = {"oi": r.call.oi, "iv": r.call.iv,
                                 "ltp": r.call.ltp, "gamma": r.call.gamma,
                                 "delta": r.call.delta}
            if r.put is not None:
                entry["put"] = {"oi": r.put.oi, "iv": r.put.iv,
                                "ltp": r.put.ltp, "gamma": r.put.gamma,
                                "delta": r.put.delta}
            rows.append(entry)
        if max_strikes is not None and snap.atm_strike is not None:
            rows.sort(key=lambda e: abs(e["strike"] - snap.atm_strike))
            rows = rows[:max_strikes]
            rows.sort(key=lambda e: e["strike"])
        return {
            "instrument_token": snap.instrument_token,
            "exchange": snap.exchange,
            "tradingsymbol": snap.tradingsymbol,
            "expiry": snap.expiry,
            "spot_price": snap.spot_price,
            "atm_strike": snap.atm_strike,
            "strikes": rows,
        }

    # ── compute_* tools ────────────────────────────────────────────────────────

    @mcp.tool(name=TOOL_COMPUTE_PCR)
    async def compute_pcr_tool(underlying: str, expiry: str | None = None) -> dict[str, Any]:
        """Put-Call Ratio from total open interest of the option chain (>1 = bearish)."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        return {"status": "ok", **compute_pcr(snap)}

    @mcp.tool(name=TOOL_COMPUTE_MAX_PAIN)
    async def compute_max_pain_tool(underlying: str, expiry: str | None = None) -> dict[str, Any]:
        """Strike where total option-writer payout is minimised (max pain theory)."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        return {"status": "ok", **compute_max_pain(snap)}

    @mcp.tool(name=TOOL_COMPUTE_TOP_OI_STRIKES)
    async def compute_top_oi_strikes_tool(underlying: str, expiry: str | None = None, n: int = 5) -> dict[str, Any]:
        """Strikes with the highest call OI and highest put OI (key battle levels)."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        return {"status": "ok", **compute_top_oi_strikes(snap, n=n)}

    @mcp.tool(name=TOOL_COMPUTE_ATM)
    async def compute_atm_tool(underlying: str, expiry: str | None = None) -> dict[str, Any]:
        """At-the-money strike and the underlying spot used."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        return {"status": "ok", **compute_atm(snap)}

    @mcp.tool(name=TOOL_COMPUTE_IV_SKEW)
    async def compute_iv_skew_tool(underlying: str, expiry: str | None = None) -> dict[str, Any]:
        """IV skew: average OTM put IV minus average OTM call IV (negative = fear)."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        return {"status": "ok", **compute_iv_skew(snap)}

    @mcp.tool(name=TOOL_COMPUTE_OI_BUILDUP)
    async def compute_oi_buildup_tool(underlying: str, expiry: str | None = None) -> dict[str, Any]:
        """Count of legs per buildup tag (Long/Short Buildup, Long Unwinding, ...)."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        return {"status": "ok", **compute_oi_buildup(snap)}

    @mcp.tool(name=TOOL_COMPUTE_SUPPORT_RESISTANCE)
    async def compute_support_resistance_tool(underlying: str, expiry: str | None = None) -> dict[str, Any]:
        """Support = strike with max put OI; resistance = strike with max call OI."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        return {"status": "ok", **compute_support_resistance(snap)}

    @mcp.tool(name=TOOL_COMPUTE_STRADDLE)
    async def compute_straddle_tool(underlying: str, expiry: str | None = None) -> dict[str, Any]:
        """ATM straddle cost and its two breakeven levels."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        return {"status": "ok", **compute_straddle(snap)}

    @mcp.tool(name=TOOL_COMPUTE_GEX)
    async def compute_gex_tool(underlying: str, expiry: str | None = None) -> dict[str, Any]:
        """Gamma Exposure proxy: net of (gamma * OI) across calls minus puts."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        return {"status": "ok", **compute_gex(snap)}

    @mcp.tool(name=TOOL_COMPUTE_FUTURES_BASIS)
    async def compute_futures_basis_tool(underlying: str) -> dict[str, Any]:
        """Futures premium/discount vs spot for each expiry (cost-of-carry).

        Requires a live underlying spot and live futures quotes; returns an empty
        contract list when those are unavailable rather than fabricating values.
        """
        if intel is None or md is None or mkt is None:
            return {"status": "error", "error": "services unavailable"}
        row = intel.resolve_underlying(underlying)
        if row is None:
            return {"status": "error", "error": f"unknown underlying '{underlying}'"}
        spot, _ = intel._spot_for(row)
        if spot is None:
            return {"status": "error", "error": "no live spot available for underlying"}
        fut = await asyncio.to_thread(intel.futures_contracts, underlying)
        contracts = fut.get("contracts") or []
        legs: list[dict[str, Any]] = []
        for c in contracts:
            key = c.get("instrument_key") or ""
            if ":" not in key:
                continue
            exch, tok = key.split(":", 1)
            quote = mkt.get_quote_now(exch, tok) if hasattr(mkt, "get_quote_now") else None
            ltp = getattr(quote, "ltp", None) if quote is not None else None
            if ltp is None:
                continue
            legs.append({"expiry": c.get("expiry"), "last_price": ltp})
        return {"status": "ok", **compute_futures_basis(legs, spot)}

    # ── price_* strategy tools ──────────────────────────────────────────────────

    @mcp.tool(name=TOOL_PRICE_LONG_STRADDLE)
    async def price_long_straddle_tool(underlying: str, expiry: str | None = None, strike: float | None = None) -> dict[str, Any]:
        """Long straddle: buy ATM call + buy ATM put. Profits on big moves either way."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        return {"status": "ok", **price_long_straddle(snap, strike=strike)}

    @mcp.tool(name=TOOL_PRICE_LONG_STRANGLE)
    async def price_long_strangle_tool(underlying: str, call_strike: float, put_strike: float, expiry: str | None = None) -> dict[str, Any]:
        """Long strangle: buy OTM call + buy OTM put. Cheaper than a straddle, needs bigger move."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        return {"status": "ok", **price_long_strangle(snap, call_strike, put_strike)}

    @mcp.tool(name=TOOL_PRICE_BULL_CALL_SPREAD)
    async def price_bull_call_spread_tool(underlying: str, lower_strike: float, higher_strike: float, expiry: str | None = None) -> dict[str, Any]:
        """Bull call spread: buy lower-strike call, sell higher-strike call. Capped upside."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        return {"status": "ok", **price_bull_call_spread(snap, lower_strike, higher_strike)}

    @mcp.tool(name=TOOL_PRICE_BEAR_PUT_SPREAD)
    async def price_bear_put_spread_tool(underlying: str, higher_strike: float, lower_strike: float, expiry: str | None = None) -> dict[str, Any]:
        """Bear put spread: buy higher-strike put, sell lower-strike put. Capped downside."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        return {"status": "ok", **price_bear_put_spread(snap, higher_strike, lower_strike)}

    @mcp.tool(name=TOOL_PRICE_IRON_CONDOR)
    async def price_iron_condor_tool(underlying: str, put_sell_strike: float, put_buy_strike: float, call_buy_strike: float, call_sell_strike: float, expiry: str | None = None) -> dict[str, Any]:
        """Iron condor: sell OTM put, buy lower put, buy OTM call, sell higher call. Range-bound income."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        return {"status": "ok", **price_iron_condor(snap, put_sell_strike, put_buy_strike, call_buy_strike, call_sell_strike)}

    @mcp.tool(name=TOOL_PRICE_LONG_BUTTERFLY)
    async def price_long_butterfly_tool(underlying: str, lower_strike: float, middle_strike: float, upper_strike: float, expiry: str | None = None) -> dict[str, Any]:
        """Long butterfly: buy lower call, sell 2 middle calls, buy upper call. Profits at middle."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        return {"status": "ok", **price_long_butterfly(snap, lower_strike, middle_strike, upper_strike)}

    # ── composite ───────────────────────────────────────────────────────────────

    @mcp.tool(name=TOOL_ANALYZE_OPTION_CHAIN)
    async def analyze_option_chain_tool(underlying: str, expiry: str | None = None, max_strikes: int | None = None) -> dict[str, Any]:
        """One-call option-chain analysis: 7 derived analytics (PCR, max pain, ATM,
        support/resistance, OI buildup, IV skew, GEX) over the FULL chain, plus an
        optional embedded chain view (trimmed to ``max_strikes`` around ATM)."""
        snap, err = await _snapshot_for(underlying, expiry)
        if err:
            return {"status": "error", **err}
        analytics = analyze_option_chain(snap)
        data: dict[str, Any] = {
            "symbol": underlying,
            "analytics": analytics,
        }
        if max_strikes is not None:
            data["chain"] = _embed_chain(snap, max_strikes=max_strikes)
        return {"status": "ok", **data}
