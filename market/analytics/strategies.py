"""Strategy pricers — pure payoff math over option premiums.

A "leg" is {strike, type: 'CE'|'PE', qty (signed: +buy/-sell), price}. These
compute max profit/loss and breakevens from the option premiums alone (intrinsic
at expiry), which is what the AI needs to compare strategies. No network, no
broker — a pure function over the same ``OptionChainSnapshot`` model as
:mod:`market.analytics.option_chain`.

The 6 named pricers build their leg specs and delegate to :func:`price_strategy`.
"""

from __future__ import annotations

from typing import Any

from market.models import OptionChainSnapshot

from market.analytics.option_chain import _atm_strike, _num


def _payoff_at(legs: list[dict[str, Any]], spot: float) -> float:
    total = 0.0
    for leg in legs:
        k = float(leg["strike"])
        typ = leg["type"]
        qty = float(leg.get("signedQty", leg.get("qty", 0)))
        price = float(leg["price"])
        intrinsic = max(spot - k, 0.0) if typ == "CE" else max(k - spot, 0.0)
        total += qty * (intrinsic - price)
    return total


def _breakevens(legs: list[dict[str, Any]]) -> list[float]:
    """Numerically find expiry breakeven spots where net payoff == 0."""
    strikes = sorted({float(l["strike"]) for l in legs})
    if not strikes:
        return []
    lo = min(strikes) * 0.5
    hi = max(strikes) * 1.5
    points = [lo + (hi - lo) * i / 200 for i in range(201)]
    payoffs = [(s, _payoff_at(legs, s)) for s in points]
    bes: list[float] = []
    for i in range(1, len(payoffs)):
        prev_s, prev_p = payoffs[i - 1]
        cur_s, cur_p = payoffs[i]
        if prev_p == 0:
            bes.append(round(prev_s, 2))
        elif prev_p * cur_p < 0:
            bes.append(round((prev_s + cur_s) / 2, 2))
    return bes


def _price_for(snapshot: OptionChainSnapshot, strike: float, otype: str) -> float:
    """Resolve the premium for a (strike, type) from the nearest listed row."""
    rows_by_strike = {r.strike: r for r in snapshot.strikes}
    if not rows_by_strike:
        return 0.0
    nearest = min(rows_by_strike, key=lambda s: abs(s - strike))
    row = rows_by_strike[nearest]
    leg = row.call if otype == "CE" else row.put
    return float(leg.ltp) if leg and leg.ltp is not None else 0.0


def price_strategy(
    strategy: str,
    snapshot: OptionChainSnapshot,
    legs_spec: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generic pricer. ``legs_spec`` = list of {strike, type, qty, side:'BUY'/'SELL'}.

    Resolves each leg's premium from the chain (by nearest strike + type), then
    computes net debit/credit, max profit, max loss, and breakevens at expiry.
    """
    atm = _atm_strike(snapshot)
    priced_legs: list[dict[str, Any]] = []
    for spec in legs_spec:
        otype = "CE" if str(spec["type"]).upper().startswith("C") else "PE"
        strike = float(spec["strike"])
        side = str(spec.get("side", "BUY")).upper()
        qty = abs(float(spec.get("qty", 1)))
        signed = qty if side == "BUY" else -qty
        price = _price_for(snapshot, strike, otype)
        priced_legs.append({
            "strike": strike,
            "type": otype,
            "side": side,
            "qty": qty,
            "price": round(price, 2),
            "signedQty": signed,
        })

    net_debit = sum(-l["signedQty"] * l["price"] for l in priced_legs)

    strikes = [l["strike"] for l in priced_legs]
    lo = min(strikes) * 0.5 if strikes else 0
    hi = max(strikes) * 1.5 if strikes else 0
    samples = [_payoff_at(priced_legs, lo + (hi - lo) * i / 100)
               for i in range(101)]
    max_p = max(samples)
    max_l = min(samples)
    return {
        "strategy": strategy,
        "underlying_value": _num(snapshot.spot_price),
        "atm_strike": atm,
        "net_debit": round(net_debit, 2),
        "max_profit": round(max_p, 2),
        "max_loss": round(max_l, 2),
        "breakevens": _breakevens(priced_legs),
        "legs": priced_legs,
    }


# ─── Named strategy pricers ────────────────────────────────────────────────────

def price_long_straddle(snapshot: OptionChainSnapshot, strike: float | None = None) -> dict[str, Any]:
    """Long straddle: buy ATM call + buy ATM put. Profits on big moves either way."""
    atm = strike or _atm_strike(snapshot)
    legs = [
        {"strike": atm, "type": "CE", "qty": 1, "side": "BUY"},
        {"strike": atm, "type": "PE", "qty": 1, "side": "BUY"},
    ]
    return price_strategy("long_straddle", snapshot, legs)


def price_long_strangle(snapshot: OptionChainSnapshot, call_strike: float, put_strike: float) -> dict[str, Any]:
    """Long strangle: buy OTM call + buy OTM put. Cheaper than a straddle, needs bigger move."""
    legs = [
        {"strike": call_strike, "type": "CE", "qty": 1, "side": "BUY"},
        {"strike": put_strike, "type": "PE", "qty": 1, "side": "BUY"},
    ]
    return price_strategy("long_strangle", snapshot, legs)


def price_bull_call_spread(snapshot: OptionChainSnapshot, lower_strike: float, higher_strike: float) -> dict[str, Any]:
    """Bull call spread: buy lower-strike call, sell higher-strike call. Capped upside."""
    legs = [
        {"strike": lower_strike, "type": "CE", "qty": 1, "side": "BUY"},
        {"strike": higher_strike, "type": "CE", "qty": 1, "side": "SELL"},
    ]
    return price_strategy("bull_call_spread", snapshot, legs)


def price_bear_put_spread(snapshot: OptionChainSnapshot, higher_strike: float, lower_strike: float) -> dict[str, Any]:
    """Bear put spread: buy higher-strike put, sell lower-strike put. Capped downside."""
    legs = [
        {"strike": higher_strike, "type": "PE", "qty": 1, "side": "BUY"},
        {"strike": lower_strike, "type": "PE", "qty": 1, "side": "SELL"},
    ]
    return price_strategy("bear_put_spread", snapshot, legs)


def price_iron_condor(
    snapshot: OptionChainSnapshot,
    put_sell_strike: float, put_buy_strike: float,
    call_buy_strike: float, call_sell_strike: float,
) -> dict[str, Any]:
    """Iron condor: sell OTM put, buy lower put, buy OTM call, sell higher call. Range-bound income."""
    legs = [
        {"strike": put_sell_strike, "type": "PE", "qty": 1, "side": "SELL"},
        {"strike": put_buy_strike, "type": "PE", "qty": 1, "side": "BUY"},
        {"strike": call_buy_strike, "type": "CE", "qty": 1, "side": "BUY"},
        {"strike": call_sell_strike, "type": "CE", "qty": 1, "side": "SELL"},
    ]
    return price_strategy("iron_condor", snapshot, legs)


def price_long_butterfly(
    snapshot: OptionChainSnapshot,
    lower_strike: float, middle_strike: float, upper_strike: float,
) -> dict[str, Any]:
    """Long butterfly: buy lower call, sell 2 middle calls, buy upper call. Profits at middle."""
    legs = [
        {"strike": lower_strike, "type": "CE", "qty": 1, "side": "BUY"},
        {"strike": middle_strike, "type": "CE", "qty": 2, "side": "SELL"},
        {"strike": upper_strike, "type": "CE", "qty": 1, "side": "BUY"},
    ]
    return price_strategy("long_butterfly", snapshot, legs)
