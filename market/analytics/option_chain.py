"""Derived F&O chain analytics — pure functions over MarketHub's OptionChainSnapshot.

Ported from TBMCP's ``analytics/option_chain.py``. Nothing here touches the
network or a broker. Every function takes an ``OptionChainSnapshot`` (the same
canonical model Upstox and Fyers normalizers produce) and returns a small dict,
so the analytics work identically whichever broker served the chain. The MCP
tools and the WebUI call these after fetching the snapshot.

Strategy pricing lives in :mod:`market.analytics.strategies`.
"""

from __future__ import annotations

from typing import Any

from market.models import OptionChainSnapshot, OptionContractData

# Buildup classification mirrors TBMCP's constants. MarketHub's snapshot does
# NOT carry a precomputed buildup_tag, so it is derived on the fly from OI
# change and price change (see classify_buildup).
NEUTRAL_BUILDUP = "Neutral"
BUILDUP_COLORS: dict[str, str] = {
    "Long Buildup": "#16a34a",
    "Short Buildup": "#dc2626",
    "Long Unwinding": "#f59e0b",
    "Short Covering": "#2563eb",
    NEUTRAL_BUILDUP: "#6b7280",
}


def _num(value: float | None, default: float = 0.0) -> float:
    return value if value is not None else default


def _leg_dict(leg: OptionContractData | None, strike: float) -> dict[str, Any] | None:
    """Map an OptionContractData leg to the TBMCP-style leg dict the analytics read."""
    if leg is None:
        return None
    oi_change = _num(leg.oi_change)
    price_change = _num(leg.ltp) - _num(leg.close)
    return {
        "strike": strike,
        "open_interest": _num(leg.oi),
        "implied_volatility": leg.iv,
        "last_price": _num(leg.ltp),
        "gamma": _num(leg.gamma),
        "buildup_tag": classify_buildup(oi_change, price_change),
    }


def _rows(snapshot: OptionChainSnapshot) -> list[dict[str, Any]]:
    """Build the per-strike row list the analytics iterate over."""
    rows: list[dict[str, Any]] = []
    for r in snapshot.strikes:
        rows.append({
            "strike": r.strike,
            "atm": r.atm,
            "call_leg": _leg_dict(r.call, r.strike),
            "put_leg": _leg_dict(r.put, r.strike),
        })
    return rows


def _atm_strike(snapshot: OptionChainSnapshot) -> float:
    """ATM strike: prefer the snapshot's flagged ATM, else nearest listed strike to spot."""
    if snapshot.atm_strike is not None:
        return snapshot.atm_strike
    spot = _num(snapshot.spot_price)
    strikes = [r.strike for r in snapshot.strikes]
    if not strikes:
        return 0.0
    return min(strikes, key=lambda s: abs(s - spot))


def classify_buildup(oi_change: float, price_change: float) -> str:
    """Classify a single option leg from its OI change and price change.

    OI up   + price up   -> Long Buildup
    OI up   + price down -> Short Buildup
    OI down + price down -> Long Unwinding
    OI down + price up   -> Short Covering
    otherwise            -> Neutral
    """
    oi_up, oi_down = oi_change > 0, oi_change < 0
    price_up, price_down = price_change > 0, price_change < 0
    if oi_up and price_up:
        return "Long Buildup"
    if oi_up and price_down:
        return "Short Buildup"
    if oi_down and price_down:
        return "Long Unwinding"
    if oi_down and price_up:
        return "Short Covering"
    return NEUTRAL_BUILDUP


def buildup_color(build_tag: str) -> str:
    """Return the display colour for a buildup tag (falls back to Neutral)."""
    return BUILDUP_COLORS.get(build_tag, BUILDUP_COLORS[NEUTRAL_BUILDUP])


def compute_pcr(snapshot: OptionChainSnapshot) -> dict[str, Any]:
    """Put-Call Ratio from total open interest. >1 = put-heavy (bearish bias)."""
    rows = _rows(snapshot)
    ce_oi = sum(r["call_leg"]["open_interest"] for r in rows if r["call_leg"])
    pe_oi = sum(r["put_leg"]["open_interest"] for r in rows if r["put_leg"])
    pcr = (pe_oi / ce_oi) if ce_oi else 0.0
    if pcr >= 1.2:
        interp = "Put-heavy — bearish sentiment / possible oversold"
    elif pcr <= 0.8:
        interp = "Call-heavy — bullish sentiment / possible overbought"
    else:
        interp = "Balanced sentiment"
    return {"pcr": round(pcr, 4), "total_call_oi": ce_oi,
            "total_put_oi": pe_oi, "interpretation": interp}


def compute_max_pain(snapshot: OptionChainSnapshot) -> dict[str, Any]:
    """Strike where total option-writer payout is minimised (max pain theory)."""
    rows = _rows(snapshot)
    strikes = [r["strike"] for r in rows]
    if not strikes:
        return {"max_pain": 0.0, "underlying_value": _num(snapshot.spot_price)}
    best_strike = strikes[0]
    best_loss: float | None = None
    for s in strikes:
        loss = 0.0
        for r in rows:
            ce = r["call_leg"]
            pe = r["put_leg"]
            if ce:
                k = ce["strike"]
                oi = ce["open_interest"]
                if s > k:
                    loss += (s - k) * oi
            if pe:
                k = pe["strike"]
                oi = pe["open_interest"]
                if s < k:
                    loss += (k - s) * oi
        if best_loss is None or loss < best_loss:
            best_loss = loss
            best_strike = s
    return {"max_pain": best_strike, "underlying_value": _num(snapshot.spot_price)}


def compute_top_oi_strikes(snapshot: OptionChainSnapshot, n: int = 5) -> dict[str, Any]:
    """Strikes with the highest call OI and highest put OI (key battle levels)."""
    rows = _rows(snapshot)
    call_rows = sorted(
        [r for r in rows if r["call_leg"]],
        key=lambda r: r["call_leg"]["open_interest"], reverse=True)[:n]
    put_rows = sorted(
        [r for r in rows if r["put_leg"]],
        key=lambda r: r["put_leg"]["open_interest"], reverse=True)[:n]
    return {
        "top_call_oi": [
            {"strike": r["strike"], "oi": r["call_leg"]["open_interest"]}
            for r in call_rows],
        "top_put_oi": [
            {"strike": r["strike"], "oi": r["put_leg"]["open_interest"]}
            for r in put_rows],
    }


def compute_atm(snapshot: OptionChainSnapshot) -> dict[str, Any]:
    """At-the-money strike and the underlying spot used."""
    return {"atm_strike": _atm_strike(snapshot),
            "underlying_value": _num(snapshot.spot_price)}


def compute_iv_skew(snapshot: OptionChainSnapshot) -> dict[str, Any]:
    """IV skew: average OTM put IV minus average OTM call IV (negative = fear)."""
    atm = _atm_strike(snapshot)
    rows = _rows(snapshot)
    call_ivs: list[float] = []
    put_ivs: list[float] = []
    for r in rows:
        if r["call_leg"] and r["strike"] > atm:
            iv = r["call_leg"]["implied_volatility"]
            if iv:
                call_ivs.append(float(iv))
        if r["put_leg"] and r["strike"] < atm:
            iv = r["put_leg"]["implied_volatility"]
            if iv:
                put_ivs.append(float(iv))
    avg_call = sum(call_ivs) / len(call_ivs) if call_ivs else 0.0
    avg_put = sum(put_ivs) / len(put_ivs) if put_ivs else 0.0
    return {
        "otm_call_avg_iv": round(avg_call, 4),
        "otm_put_avg_iv": round(avg_put, 4),
        "skew": round(avg_put - avg_call, 4),
    }


def compute_oi_buildup(snapshot: OptionChainSnapshot) -> dict[str, Any]:
    """Count of legs per buildup tag (Long/Short Buildup, Long Unwinding, ...).

    MarketHub does not store a buildup_tag, so it is derived on the fly from each
    leg's OI change and price change via classify_buildup (the same logic TBMCP
    applies at ingest).
    """
    rows = _rows(snapshot)
    counts: dict[str, int] = {}
    total = 0
    for r in rows:
        for side in ("call_leg", "put_leg"):
            leg = r[side]
            if leg:
                tag = leg["buildup_tag"] or NEUTRAL_BUILDUP
                counts[tag] = counts.get(tag, 0) + 1
                total += 1
    return {"buildup_counts": counts, "total_legs": total}


def compute_support_resistance(snapshot: OptionChainSnapshot) -> dict[str, Any]:
    """Support = strike with max put OI; resistance = strike with max call OI."""
    rows = _rows(snapshot)
    max_put: tuple[float, int] | None = None
    max_call: tuple[float, int] | None = None
    for r in rows:
        if r["put_leg"]:
            oi = int(r["put_leg"]["open_interest"] or 0)
            if max_put is None or oi > max_put[1]:
                max_put = (float(r["strike"]), oi)
        if r["call_leg"]:
            oi = int(r["call_leg"]["open_interest"] or 0)
            if max_call is None or oi > max_call[1]:
                max_call = (float(r["strike"]), oi)
    return {
        "support": max_put[0] if max_put else 0.0,
        "support_oi": max_put[1] if max_put else 0,
        "resistance": max_call[0] if max_call else 0.0,
        "resistance_oi": max_call[1] if max_call else 0,
    }


def compute_straddle(snapshot: OptionChainSnapshot) -> dict[str, Any]:
    """ATM straddle cost and its two breakeven levels."""
    atm = _atm_strike(snapshot)
    rows = _rows(snapshot)
    row = next((r for r in rows if r["strike"] == atm), None)
    if not row or not row["call_leg"] or not row["put_leg"]:
        return {"atm_strike": atm, "straddle_cost": 0.0,
                "upper_breakeven": 0.0, "lower_breakeven": 0.0}
    cost = row["call_leg"]["last_price"] + row["put_leg"]["last_price"]
    return {
        "atm_strike": atm,
        "straddle_cost": round(cost, 2),
        "upper_breakeven": round(atm + cost, 2),
        "lower_breakeven": round(atm - cost, 2),
    }


def compute_gex(snapshot: OptionChainSnapshot) -> dict[str, Any]:
    """Gamma Exposure proxy: net of (gamma * OI) across calls minus puts."""
    rows = _rows(snapshot)
    call_gex = 0.0
    put_gex = 0.0
    for r in rows:
        if r["call_leg"]:
            g = r["call_leg"]["gamma"] or 0.0
            call_gex += g * r["call_leg"]["open_interest"]
        if r["put_leg"]:
            g = r["put_leg"]["gamma"] or 0.0
            put_gex += g * r["put_leg"]["open_interest"]
    net = call_gex - put_gex
    return {
        "call_gamma_exposure": round(call_gex, 2),
        "put_gamma_exposure": round(put_gex, 2),
        "net_gex": round(net, 2),
        "interpretation": ("positive (dealers long gamma, stabilising)"
                           if net > 0 else "negative (dealers short gamma, amplifying)"),
    }


def compute_futures_basis(futures_legs: list[dict[str, Any]], spot: float) -> dict[str, Any]:
    """Futures premium/discount vs spot for each expiry (carry / cost-of-carry).

    ``futures_legs`` is a list of ``{"expiry": str, "last_price": float}`` built
    by the caller (typically from the underlying's futures contracts + live
    quotes). Provider-agnostic.
    """
    contracts = []
    for l in futures_legs:
        fut = float(l.get("last_price") or 0)
        basis = fut - spot
        pct = (basis / spot * 100) if spot else 0.0
        contracts.append({
            "expiry": l.get("expiry"),
            "future_price": round(fut, 2),
            "spot": round(spot, 2),
            "basis": round(basis, 2),
            "basis_pct": round(pct, 4),
        })
    return {"spot": round(spot, 2), "contracts": contracts}


def analyze_option_chain(snapshot: OptionChainSnapshot) -> dict[str, Any]:
    """One-call option-chain analysis: PCR, max pain, ATM, support/resistance,
    OI buildup, IV skew and GEX — all derived locally over the FULL chain.

    Mirrors the analytics block of TBMCP's ``analyze_option_chain`` tool so an
    AI client gets the same derived view. Returns ``{source, ...}`` dict.
    """
    return {
        "source": "derived",
        "pcr": compute_pcr(snapshot),
        "max_pain": compute_max_pain(snapshot),
        "atm": compute_atm(snapshot),
        "support_resistance": compute_support_resistance(snapshot),
        "oi_buildup": compute_oi_buildup(snapshot),
        "iv_skew": compute_iv_skew(snapshot),
        "gex": compute_gex(snapshot),
    }
