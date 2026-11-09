"""FYERS HSM wire-symbol resolution (official SDK contract).

Source of truth: the official FYERS SDK assets
``fyers_apiv3/FyersWebsocket/map.json`` and
``fyers_apiv3/FyersWebsocket/data_ws.py`` (the package published on
PyPI as ``fyers-apiv3``). The SDK resolves an API symbol to an HSM
topic as follows:

* ``POST https://api-t1.fyers.in/data/symbol-token`` with
  ``{"symbols": [...]}`` returns ``validSymbol`` (symbol -> fyToken)
  and ``invalidSymbol``;
* the first four digits of the fyToken select the exchange segment
  (``exch_seg_dict``);
* an equity/option symbol becomes ``sf|<segment>|<fyToken[10:]>``;
* an index symbol becomes ``if|<segment>|<index topic>`` where the
  topic name comes from the SDK's ``index_dict``.

A symbol that has no documented HSM topic (an index that is not in
``index_dict``) is NOT invented here: it is reported as unresolved so
the caller can serve it over the REST path instead. The same is true
for a symbol the broker rejects (``invalidSymbol``).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: The official symbol-token endpoint used by the FYERS SDK.
SYMBOL_TOKEN_URL = "https://api-t1.fyers.in/data/symbol-token"

#: Symbols per symbol-token request. The endpoint is a Data API (5 rps
#: / 50 rmin / 5,000 per day on Standard) and the documented Quotes
#: batch size is 50 symbols, so the same conservative batch is used
#: here to stay inside the request-rate budget.
SYMBOL_TOKEN_BATCH = 50

#: fyToken prefix (first four digits) -> HSM exchange segment.
#: (official SDK ``exch_seg_dict``)
EXCH_SEG: dict[str, str] = {
    '1010': 'nse_cm',
    '1011': 'nse_fo',
    '1012': 'cde_fo',
    '1020': 'nse_com',
    '1120': 'mcx_fo',
    '1210': 'bse_cm',
    '1211': 'bse_fo',
    '1212': 'bcs_fo',
}

#: Index API symbol -> HSM index topic name.
#: (official SDK ``index_dict``) - a symbol that is absent here has no
#: documented HSM index topic and must not be streamed.
INDEX_TOPICS: dict[str, str] = {
    'NSE:NIFTYINDIAMFG-INDEX': 'NIFTY INDIA MFG',
    'NSE:NIFTY100ESG-INDEX': 'NIFTY100 ESG',
    'NSE:NIFTYINDDIGITAL-INDEX': 'NIFTY IND DIGITAL',
    'NSE:NIFTYMICROCAP250-INDEX': 'NIFTY MICROCAP250',
    'NSE:NIFTYCONSRDURBL-INDEX': 'NIFTY CONSR DURBL',
    'NSE:NIFTYHEALTHCARE-INDEX': 'NIFTY HEALTHCARE',
    'NSE:NIFTYOILANDGAS-INDEX': 'NIFTY OIL AND GAS',
    'NSE:NIFTY100ESGSECLDR-INDEX': 'Nifty100ESGSecLdr',
    'NSE:NIFTY200MOMENTM30-INDEX': 'Nifty200Momentm30',
    'NSE:NIFTYALPHALOWVOL-INDEX': 'NIFTY AlphaLowVol',
    'NSE:NIFTY200QUALTY30-INDEX': 'NIFTY200 QUALTY30',
    'NSE:NIFTYSMLCAP50-INDEX': 'NIFTY SMLCAP 50',
    'NSE:MIDCPNIFTY-INDEX': 'NIFTY MID SELECT',
    'NSE:NIFTYMIDSELECT-INDEX': 'NIFTY MID SELECT',
    'NSE:NIFTYMIDCAP150-INDEX': 'NIFTY MIDCAP 150',
    'NSE:NIFTY100 EQL WGT-INDEX': 'NIFTY100 EQL Wgt',
    'NSE:NIFTY50 EQL WGT-INDEX': 'NIFTY50 EQL Wgt',
    'NSE:NIFTYGSCOMPSITE-INDEX': 'Nifty GS Compsite',
    'NSE:NIFTYGS1115YR-INDEX': 'Nifty GS 11 15Yr',
    'NSE:NIFTYGS48YR-INDEX': 'Nifty GS 4 8Yr',
    'NSE:NIFTYGS10YRCLN-INDEX': 'Nifty GS 10Yr Cln',
    'NSE:NIFTYGS813YR-INDEX': 'Nifty GS 8 13Yr',
    'NSE:NIFTYSMLCAP100-INDEX': 'NIFTY SMLCAP 100',
    'NSE:NIFTYQUALITY30-INDEX': 'NIFTY100 Qualty30',
    'NSE:NIFTYPVTBANK-INDEX': 'Nifty Pvt Bank',
    'NSE:NIFTYPHARMA-INDEX': 'Nifty Pharma',
    'NSE:NIFTYLARGEMID250-INDEX': 'NIFTY LARGEMID250',
    'NSE:NIFTYGS15YRPLUS-INDEX': 'Nifty GS 15YrPlus',
    'NSE:NIFTYPSUBANK-INDEX': 'Nifty PSU Bank',
    'NSE:NIFTYSMLCAP250-INDEX': 'NIFTY SMLCAP 250',
    'NSE:NIFTYENERGY-INDEX': 'Nifty Energy',
    'NSE:NIFTYALPHA50-INDEX': 'NIFTY Alpha 50',
    'NSE:NIFTYPSE-INDEX': 'Nifty PSE',
    'NSE:NIFTYFINSRV2550-INDEX': 'Nifty FinSrv25 50',
    'NSE:FINNIFTY-INDEX': 'Nifty Fin Service',
    'NSE:NIFTYREALTY-INDEX': 'Nifty Realty',
    'NSE:NIFTY500-INDEX': 'Nifty 500',
    'NSE:NIFTY500MULTICAP-INDEX': 'NIFTY500 MULTICAP',
    'NSE:NIFTYMIDCAP50-INDEX': 'Nifty Midcap 50',
    'NSE:NIFTYTOTALMKT-INDEX': 'NIFTY TOTAL MKT',
    'NSE:NIFTY50PR2XLEV-INDEX': 'Nifty50 PR 2x Lev',
    'NSE:INDIAVIX-INDEX': 'India VIX',
    'NSE:NIFTYDIVOPPS50-INDEX': 'Nifty Div Opps 50',
    'NSE:NIFTYMNC-INDEX': 'Nifty MNC',
    'NSE:NIFTY50VALUE20-INDEX': 'Nifty50 Value 20',
    'NSE:NIFTY50-INDEX': 'Nifty 50',
    'NSE:HANGSENG BEES-NAV-INDEX': 'HangSeng BeES-NAV',
    'NSE:NIFTY100LIQ15-INDEX': 'Nifty100 Liq 15',
    'NSE:NIFTY50TR2XLEV-INDEX': 'Nifty50 TR 2x Lev',
    'NSE:NIFTY100-INDEX': 'Nifty 100',
    'NSE:NIFTY100 LOWVOL30-INDEX': 'NIFTY100 LowVol30',
    'NSE:NIFTYBANK-INDEX': 'Nifty Bank',
    'NSE:NIFTYFMCG-INDEX': 'Nifty FMCG',
    'NSE:NIFTYIT-INDEX': 'Nifty IT',
    'NSE:NIFTYGS10YR-INDEX': 'Nifty GS 10Yr',
    'NSE:NIFTYMIDCAP100-INDEX': 'NIFTY MIDCAP 100',
    'NSE:NIFTYNEXT50-INDEX': 'Nifty Next 50',
    'NSE:NIFTYM150QLTY50-INDEX': 'NIFTY M150 QLTY50',
    'NSE:NIFTYSERVSECTOR-INDEX': 'Nifty Serv Sector',
    'NSE:NIFTYMIDSML400-INDEX': 'NIFTY MIDSML 400',
    'NSE:NIFTYAUTO-INDEX': 'Nifty Auto',
    'NSE:NIFTYMETAL-INDEX': 'Nifty Metal',
    'NSE:NIFTYINFRA-INDEX': 'Nifty Infra',
    'NSE:NIFTYMEDIA-INDEX': 'Nifty Media',
    'NSE:NIFTY50PR1XINV-INDEX': 'Nifty50 PR 1x Inv',
    'NSE:NIFTY200-INDEX': 'Nifty 200',
    'NSE:NIFTY50TR1XINV-INDEX': 'Nifty50 TR 1x Inv',
    'NSE:NIFTYCPSE-INDEX': 'Nifty CPSE',
    'NSE:NIFTYMIDLIQ15-INDEX': 'Nifty Mid Liq 15',
    'NSE:NIFTYCOMMODITIES-INDEX': 'Nifty Commodities',
    'NSE:NIFTYCONSUMPTION-INDEX': 'Nifty Consumption',
    'NSE:NIFTY50DIVPOINT-INDEX': 'Nifty50 Div Point',
    'NSE:NIFTYGROWSECT15-INDEX': 'Nifty GrowSect 15',
    'BSE:100LARGECAPTMC-INDEX': 'LCTMCI',
    'BSE:DFRG-INDEX': 'DFRGRI',
    'BSE:QUALITY-INDEX': 'BSEQUI',
    'BSE:DIVIDENDSTABILITY-INDEX': 'BSEDSI',
    'BSE:250SMALLCAP-INDEX': 'SML250',
    'BSE:150MIDCAP-INDEX': 'MID150',
    'BSE:ESG100-INDEX': 'ESG100',
    'BSE:SNXT50-INDEX': 'SNXT50',
    'BSE:SNSX50-INDEX': 'SNSX50',
    'BSE:UTILS-INDEX': 'UTILS',
    'BSE:GREENEX-INDEX': 'GREENX',
    'BSE:SENSEX-INDEX': 'SENSEX',
    'BSE:REALTY-INDEX': 'REALTY',
    'BSE:PRIVATEBANKS-INDEX': 'BSEPBI',
    'BSE:CDGS-INDEX': 'CDGS',
    'BSE:OILGAS-INDEX': 'OILGAS',
    'BSE:ENERGY-INDEX': 'ENERGY',
    'BSE:POWER-INDEX': 'POWER',
    'BSE:500-INDEX': 'BSE500',
    'BSE:100-INDEX': 'BSE100',
    'BSE:PSU-INDEX': 'BSEPSU',
    'BSE:HC-INDEX': 'BSE HC',
    'BSE:400MIDSMALLCAP-INDEX': 'MSL400',
    'BSE:BHRT22-INDEX': 'BHRT22',
    'BSE:BANKEX-INDEX': 'BANKEX',
    'BSE:ALLCAP-INDEX': 'ALLCAP',
    'BSE:INFRA-INDEX': 'INFRA',
    'BSE:CD-INDEX': 'BSE CD',
    'BSE:MIDCAP-INDEX': 'MIDCAP',
    'BSE:AUTO-INDEX': 'AUTO',
    'BSE:BASMTR-INDEX': 'BASMTR',
    'BSE:200-INDEX': 'BSE200',
    'BSE:FIN-INDEX': 'FIN',
    'BSE:CG-INDEX': 'BSE CG',
    'BSE:ENHANCEDVALUE-INDEX': 'BSEEVI',
    'BSE:TECK-INDEX': 'TECK',
    'BSE:METAL-INDEX': 'METAL',
    'BSE:CARBONEX-INDEX': 'CARBON',
    'BSE:MIDSEL-INDEX': 'MIDSEL',
    'BSE:SME IPO-INDEX': 'SMEIPO',
    'BSE:MOMENTUM-INDEX': 'BSEMOI',
    'BSE:TELCOM-INDEX': 'TELCOM',
    'BSE:CPSE-INDEX': 'CPSE',
    'BSE:250LARGEMIDCAP-INDEX': 'LMI250',
    'BSE:SMLCAP-INDEX': 'SMLCAP',
    'BSE:IT-INDEX': 'BSE IT',
    'BSE:INDIAMANUFACTURING-INDEX': 'MFG',
    'BSE:INDSTR-INDEX': 'INDSTR',
    'BSE:LOWVOLATILITY-INDEX': 'BSELVI',
    'BSE:LRGCAP-INDEX': 'LRGCAP',
    'BSE:IPO-INDEX': 'BSEIPO',
    'BSE:FMC-INDEX': 'BSEFMC',
    'BSE:SMLSEL-INDEX': 'SMLSEL',
}


def segment_for(fytoken: str | None) -> str | None:
    """HSM exchange segment for a fyToken (None when unknown)."""
    if not fytoken or len(fytoken) < 4:
        return None
    return EXCH_SEG.get(fytoken[:4])


def is_index_symbol(symbol: str) -> bool:
    """True for an index API symbol (``EXCH:NAME-INDEX``)."""
    return str(symbol or "").split("-")[-1] == "INDEX"


def wire_symbol(symbol: str, fytoken: str | None) -> tuple[str | None, str]:
    """The HSM wire topic for one API symbol.

    Returns ``(topic, reason)``. ``topic`` is None when the symbol has
    no documented HSM representation - the reason explains why, so the
    caller can report it instead of putting an invalid symbol on the
    wire.
    """
    symbol = str(symbol or "")
    if not symbol:
        return None, "empty symbol"
    segment = segment_for(fytoken)
    if segment is None:
        return None, "unknown exchange segment for fyToken"
    if is_index_symbol(symbol):
        topic = INDEX_TOPICS.get(symbol)
        if topic is None:
            return None, ("no official HSM index topic for this "
                        "symbol - serve it over REST")
        return f"if|{segment}|{topic}", ""
    if not fytoken or len(fytoken) <= 10:
        return None, "missing fyToken exchange token"
    return f"sf|{segment}|{fytoken[10:]}", ""


class SymbolTokenAuthError(RuntimeError):
    """The symbol-token endpoint rejected the access token (401/403).

    The feed must surface this as "Daily Login Required" instead of
    retrying against a dead token.
    """

    def __init__(self, status: int) -> None:
        super().__init__(f"symbol-token auth failed ({status})")
        self.status = int(status)


@dataclass
class Resolution:
    """Outcome of one symbol-token resolution pass."""

    #: API symbol -> HSM wire topic
    resolved: dict[str, str] = field(default_factory=dict)
    #: API symbol -> why it could not be resolved
    unresolved: dict[str, str] = field(default_factory=dict)

    def merge(self, other: Resolution) -> Resolution:
        self.resolved.update(other.resolved)
        self.unresolved.update(other.unresolved)
        return self


def _post_symbol_token(token: str, symbols: list[str],
                   fetch: Callable[[str, list[str]], dict[str, Any]] | None = None,
                   ) -> dict[str, Any]:
    """Call the symbol-token endpoint (one batch)."""
    # The symbol-token endpoint is a FYERS Data API: it shares the
    # provider's request budget with quotes/depth/history/option-chain.
    from providers.rate import BudgetExhausted, get_budget_manager
    try:
        get_budget_manager().acquire("fyers", "data")
    except BudgetExhausted as exc:
        # The shared Data-API budget is exhausted - surface the provider's
        # rate-limit error (the caller's contract is unchanged).
        from brokers.fyers.rest import FyersRateLimitError
        raise FyersRateLimitError(
            "fyers request budget exhausted", status_code=429) from exc
    if fetch is not None:
        return fetch(token, symbols)
    bare = token.split(":", 1)[1] if ":" in token else token
    req = urllib.request.Request(
        SYMBOL_TOKEN_URL,
        data=json.dumps({"symbols": symbols}).encode(),
        method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": bare,
                 "User-Agent": "MarketHub/1.0 (trading-terminal)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if getattr(exc, "code", None) in (401, 403):
            raise SymbolTokenAuthError(exc.code) from exc
        if getattr(exc, "code", None) == 429:
            try:
                get_budget_manager().report_block("fyers", "data")
            except Exception:
                pass
        raise


def resolve_wire_symbols(
    symbols: list[str],
    token: str,
    *,
    fetch: Callable[[str, list[str]], dict[str, Any]] | None = None,
    batch_size: int = SYMBOL_TOKEN_BATCH,
) -> Resolution:
    """Resolve API symbols to HSM wire topics.

    Any subset can be resolved (not only the initial desired set), so a
    dynamically added instrument is subscribed with a VALID wire token.
    A symbol that cannot be resolved is reported - it is never put on
    the wire as a raw API identifier.
    """
    resolution = Resolution()
    wanted = [s for s in dict.fromkeys(symbols or []) if s]
    if not wanted:
        return resolution
    size = max(1, int(batch_size))
    for start in range(0, len(wanted), size):
        chunk = wanted[start:start + size]
        try:
            payload = _post_symbol_token(token, chunk, fetch)
        except SymbolTokenAuthError:
            # The access token is rejected - the caller must surface it
            # (auth required), never treat it as "symbol not resolvable".
            raise
        except Exception as exc:
            # A budget refusal is the CALLER's problem, not a per-symbol
            # failure: it must not be swallowed as "unresolved".
            from brokers.fyers.rest import FyersRateLimitError
            if isinstance(exc, FyersRateLimitError):
                raise
            for sym in chunk:
                resolution.unresolved[sym] = (
                    f"symbol-token lookup failed: {type(exc).__name__}")
            continue
        if not isinstance(payload, dict):
            for sym in chunk:
                resolution.unresolved[sym] = "invalid symbol-token response"
            continue
        if str(payload.get("s") or "").lower() != "ok":
            message = str(payload.get("message") or "symbol-token error")
            for sym in chunk:
                resolution.unresolved[sym] = f"symbol-token error: {message}"
            continue
        valid = payload.get("validSymbol") or {}
        invalid = payload.get("invalidSymbol") or []
        invalid_set = {str(s) for s in invalid}
        for sym in chunk:
            fytoken = valid.get(sym)
            if not fytoken:
                resolution.unresolved[sym] = (
                    "broker rejected the symbol" if sym in invalid_set
                    else "symbol not returned by symbol-token")
                continue
            topic, reason = wire_symbol(sym, str(fytoken))
            if topic is None:
                resolution.unresolved[sym] = reason
                continue
            resolution.resolved[sym] = topic
    return resolution
