# Broker Data Gap Closure Plan

## Overview
This plan addresses the remaining gaps in MarketHub's broker data coverage after the comprehensive audit.

---

## GAP 1: Margin Calculator (Upstox Only)

### API Details
- **Endpoint**: `POST /v2/charges/margin`
- **Auth**: OAuth bearer token required
- **Max instruments**: 20 per request

### Request Format
```json
{
  "instruments": [
    {
      "instrument_key": "NSE_EQ|INE669E01016",
      "quantity": 1,
      "transaction_type": "BUY",
      "product": "D",
      "price": 150.0
    }
  ]
}
```

### Response Format
```json
{
  "data": {
    "margins": [
      {
        "span_margin": 0,
        "exposure_margin": 0,
        "equity_margin": 33.6,
        "total_margin": 33.6,
        ...
      }
    ],
    "required_margin": 33.6,
    "final_margin": 33.6
  }
}
```

### Implementation Plan
1. Add `MarginEntry`, `MarginBasket` models to `market/models.py`
2. Create `market/normalize/upstox_margins.py` normalizer
3. Add `margin()` method to `app/market_data.py`
4. Add `POST /api/margin` route to `api/product_routes.py`
5. Add tests

---

## GAP 2: Shareholdings (Upstox Only)

### API Details
- **Endpoint**: `GET /v2/fundamentals/:isin/share-holdings`
- **Auth**: OAuth bearer token required

### Response Format
```json
{
  "data": [
    {
      "category": "promoters",
      "history": [
        {"period": "Mar 2026", "value": 50.0},
        {"period": "Dec 2025", "value": 50.01}
      ]
    },
    {
      "category": "fii",
      "history": [...]
    }
  ]
}
```

### Implementation Plan
1. Add `ShareholdingRecord`, `ShareholdingCategory`, `Shareholdings` models
2. Extend `upstox_fundamentals.py` with `shareholdings_from_rest()`
3. Add `shareholdings()` method to `app/market_data.py`
4. Add `GET /api/fundamentals/shareholdings/:isin` route
5. Add tests

---

## GAP 3: Option Greeks Standalone (Upstox Only)

### API Details
- **Endpoint**: `GET /v3/market-quote/option-greek?instrument_key=...`
- **Auth**: OAuth bearer token required
- **Max instruments**: 50 per request

### Response Format
```json
{
  "data": {
    "NSE_FO|43885": {
      "last_price": 412.2,
      "delta": -0.8081,
      "gamma": 0.0005,
      "theta": -51.848,
      "vega": 3.3899,
      "iv": 0.336,
      "oi": 2476650,
      "volume": 3609600
    }
  }
}
```

### Implementation Plan
1. Add `OptionGreekSnapshot` model
2. Create `market/normalize/upstox_option_greeks.py` normalizer
3. Add `option_greeks()` method to `app/market_data.py`
4. Add `GET /api/market-quote/option-greek` route
5. **Do NOT add MCP tool** (per directive: no MCP changes)
6. Add tests

---

## GAP 4: Fyers Live Feed Verification

### Current Status
- **File**: `brokers/fyers/feed.py` (39KB, fully implemented)
- **Protocol**: HSM binary datafeed (wss://socket.fyers.in/hsm/v1-5/prod)
- **Features**: 
  - Symbol updates (LTP, OHLC, OI, depth)
  - Depth updates (dp topic)
  - Auth, mode, subscribe frames
  - Reconnection logic

### Verification Plan
1. Review `feed.py` for any TODOs or incomplete sections
2. Run existing tests: `test/test_fyers_feed.py`
3. Check if all `_DATA_VAL` fields are properly mapped
4. Verify depth emission is working (recently fixed)

### Expected Outcome
- If tests pass and code is complete → Mark as ✅ Verified
- If issues found → Fix and retest

---

## GAP 5: Fyers PCR/MaxPain/OI (Computed Locally)

### Current Status
- Fyers does NOT provide OI analytics APIs
- MarketHub already computes these locally in `market_intel.py`:
  - `compute_pcr()` - from option chain data
  - `compute_max_pain()` - from option chain data
  - `compute_oi_buildup()` - from option chain data

### Implementation Plan
1. Verify local computation is working correctly
2. Ensure results are returned when querying Fyers options chain
3. Document that these are derived, not native

---

## IMPLEMENTATION ORDER

### Phase 1: Quick Wins (Shareholdings + Greeks)
- [ ] Add Shareholdings model + normalizer + route
- [ ] Add Option Greeks standalone model + normalizer + route
- **Estimate**: 2 hours

### Phase 2: Margin Calculator
- [ ] Add Margin models + normalizer + route
- **Estimate**: 1.5 hours

### Phase 3: Fyers Verification
- [ ] Review and verify Fyers live feed
- [ ] Fix any issues found
- **Estimate**: 1 hour

### Phase 4: Tests & CI
- [ ] Add comprehensive tests for all new endpoints
- [ ] Run full regression
- **Estimate**: 1.5 hours

**Total Estimate**: ~6 hours

---

## FILES TO CREATE/MODIFY

### New Files
```
market/normalize/upstox_margins.py          # Margin calculator normalizer
market/normalize/upstox_option_greeks.py    # Option Greeks normalizer
test/test_broker_margins.py                 # Margin tests
test/test_broker_shareholdings.py           # Shareholdings tests
test/test_broker_option_greeks.py           # Option Greeks tests
```

### Modified Files
```
market/models.py                 # +4 new models
app/market_data.py               # +3 new methods
api/product_routes.py            # +3 new routes
test/test_broker_extended.py     # Extend existing tests
```

---

## ACCEPTANCE CRITERIA

- [ ] All new models import correctly
- [ ] All normalizers pass unit tests
- [ ] All REST endpoints return correct JSON
- [ ] Fast test group passes
- [ ] Full regression passes
- [ ] CI green

---

## NOTES

1. **No MCP tools** - Per directive, do not add MCP tools for new endpoints
2. **No WebUI changes** - Keep focus on data layer only
3. **Fyers limitations** - Clearly document what Fyers cannot provide
4. **Provider parity** - Where possible, normalize to same canonical models
