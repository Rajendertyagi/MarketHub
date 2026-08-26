# MarketHub Broker Data Completion Plan

## Overview
This plan implements all missing Upstox data APIs and models to achieve full broker data coverage.

---

## PRIORITY 1: Core Market Information (Quick Wins)

### 1.1 Market Holidays
**Endpoint:** `GET /v2/market/holidays[/:date]`
**Model:** `MarketHoliday`
```python
@dataclass
class MarketHoliday:
    date: str  # YYYY-MM-DD
    description: str
    holiday_type: str  # TRADING_HOLIDAY, SETTLEMENT_HOLIDAY, SPECIAL_TIMING
    closed_exchanges: tuple[str, ...]
    open_exchanges: tuple[str, ...]  # exchanges with special timings
```
**Files to create/modify:**
- `market/models.py` — add `MarketHoliday`
- `market/normalize/upstox_market_info.py` — new normalizer
- `app/market_data.py` — add `holidays()` method
- `api/product_routes.py` — add `GET /api/market/holidays`
- `test/test_broker_market_info.py` — tests

### 1.2 Market Session Timings
**Endpoint:** `GET /v2/market/timings/:date`
**Model:** `MarketSession`
```python
@dataclass
class MarketSession:
    exchange: str
    start_time: datetime
    end_time: datetime
```
**Files:**
- `market/models.py` — add `MarketSession`
- `market/normalize/upstox_market_info.py` — extend normalizer
- `app/market_data.py` — add `timings()` method
- `api/product_routes.py` — add `GET /api/market/timings`

---

## PRIORITY 2: Futures & Margin Analytics

### 2.1 Futures Smartlist
**Endpoint:** `GET /v2/market/smartlist/futures`
**Parameters:** `asset_type`, `category`, `page_number`, `page_size`
**Categories:**
- INDEX/STOCK: `TOP_TRADED`, `MOST_ACTIVE`, `OI_GAINERS`, `OI_LOSERS`, `PRICE_GAINERS`, `PRICE_LOSERS`, `PREMIUM`, `DISCOUNT`
- COMMODITY: `TOP_TRADED`, `MOST_ACTIVE`, `OI_GAINERS`, `OI_LOSERS`

**Model:** `FuturesSmartlistEntry`
```python
@dataclass
class FuturesSmartlistEntry:
    instrument_key: str
    price_current: float
    price_close: float
    price_change_abs: float
    price_change_pct: float
    metric_current: float
    metric_previous: float
    metric_change_abs: float
    metric_change_pct: float
    metric_key: str
```

**Files:**
- `market/models.py` — add `FuturesSmartlist`, `FuturesSmartlistEntry`
- `market/normalize/upstox_analytics.py` — add `futures_smartlist_from_rest()`
- `app/market_data.py` — add `futures_smartlist()` method
- `api/product_routes.py` — add `GET /api/futures/smartlist`

### 2.2 Margin Calculator
**Endpoint:** `POST /v2/charges/margin`
**Parameters:** List of instruments with `instrument_key`, `quantity`, `transaction_type`, `product`, optional `price`

**Model:** `MarginDetails`
```python
@dataclass
class MarginEntry:
    instrument_key: str
    span_margin: float
    exposure_margin: float
    equity_margin: float
    net_buy_premium: float
    additional_margin: float
    tender_margin: float
    total_margin: float

@dataclass
class MarginBasket:
    required_margin: float
    final_margin: float
    margins: tuple[MarginEntry, ...]
```

**Files:**
- `market/models.py` — add `MarginEntry`, `MarginBasket`
- `market/normalize/upstox_margins.py` — NEW normalizer module
- `app/market_data.py` — add `margin()` method
- `api/product_routes.py` — add `POST /api/margin`

---

## PRIORITY 3: Institutional Flow Data

### 3.1 FII Activity Data
**Endpoint:** `GET /v2/market/fii`
**Parameters:** `data_type`, `interval`, optional `from`
**Data types:** `NSE_FO|INDEX_FUTURES`, `NSE_FO|STOCK_FUTURES`, `NSE_FO|INDEX_OPTIONS`, `NSE_FO|STOCK_OPTIONS`, `NSE_EQ|CASH`
**Intervals:** `1D` (daily, 30 days), `1M` (monthly, 12 months)

**Model:** `FIIRecord`
```python
@dataclass
class FIIRecord:
    timestamp: datetime
    buy_amount: float
    sell_amount: float
    buy_contracts: int
    sell_contracts: int
    oi_contracts: int
    oi_amount: float
    total_long_contracts: int
    total_short_contracts: int
    total_call_long_contracts: int
    total_put_long_contracts: int
    total_call_short_contracts: int
    total_put_short_contracts: int
```

**Files:**
- `market/models.py` — add `FIIRecord`, `FIIActivity`
- `market/normalize/upstox_analytics.py` — add `fii_from_rest()`
- `app/market_data.py` — add `fii()` method
- `api/product_routes.py` — add `GET /api/fii`

### 3.2 DII Activity Data
**Endpoint:** `GET /v2/market/dii`
**Parameters:** `data_type` (only `NSE_EQ|CASH`), `interval`, optional `from`

**Model:** `DIIRecord` (same structure as FIIRecord)

**Files:**
- `market/models.py` — add `DIIRecord`, `DIIActivity`
- `market/normalize/upstox_analytics.py` — add `dii_from_rest()`
- `app/market_data.py` — add `dii()` method
- `api/product_routes.py` — add `GET /api/dii`

---

## PRIORITY 4: Company Fundamentals

### 4.1 Company Profile
**Endpoint:** `GET /v2/fundamentals/:isin/profile`
**Model:** `CompanyProfile`
```python
@dataclass
class CompanyProfile:
    isin: str
    company_profile: str
    sector: str
    sector_market_cap_inr: float  # in crore
    sector_market_cap_usd: float  # in billion
```

### 4.2 Key Ratios
**Endpoint:** `GET /v2/fundamentals/:isin/ratios`
**Model:** `KeyRatios`
```python
@dataclass
class KeyRatios:
    isin: str
    pe_ratio: float | None
    pb_ratio: float | None
    roe: float | None
    roa: float | None
    roce: float | None
    ev_ebitda: float | None
```

### 4.3 Corporate Actions
**Endpoint:** `GET /v2/fundamentals/:isin/corporate-actions`
**Model:** `CorporateAction`

### 4.4 Competitors
**Endpoint:** `GET /v2/fundamentals/:isin/competitors`
**Model:** `Competitor`

**Files for all fundamentals:**
- `market/models.py` — add all fundamental models
- `market/normalize/upstox_fundamentals.py` — NEW normalizer module
- `app/market_data.py` — add methods: `company_profile()`, `key_ratios()`, `corporate_actions()`, `competitors()`
- `api/product_routes.py` — add routes
- `test/test_broker_fundamentals.py` — tests

---

## PRIORITY 5: Enhanced News

### 5.1 News with Pagination
**Current:** Basic news fetching
**Enhancement:** Add category support, pagination, and filtering

**API Parameters:**
- `category`: `instrument_keys`, `positions`, `holdings`
- `instrument_keys`: comma-separated (max 30)
- `page_number`: int
- `page_size`: int (max 100)

**Files:**
- `market/normalize/upstox_news.py` — enhance normalizer
- `app/market_data.py` — update `news()` method signature
- `api/product_routes.py` — update `/api/news` route

---

## IMPLEMENTATION ORDER

### Phase 1: Foundation (Models + Normalizers)
1. Add all new models to `market/models.py`
2. Create `market/normalize/upstox_market_info.py`
3. Create `market/normalize/upstox_margins.py`
4. Create `market/normalize/upstox_fundamentals.py`
5. Extend `market/normalize/upstox_analytics.py`
6. Extend `market/normalize/upstox_news.py`

### Phase 2: Service Layer
7. Add all new methods to `app/market_data.py`
8. Add URLs/constants

### Phase 3: API Routes
9. Add all new routes to `api/product_routes.py`
10. Add helper dict converters

### Phase 4: Tests
11. Create `test/test_broker_market_info.py`
12. Create `test/test_broker_margins.py`
13. Create `test/test_broker_analytics_extended.py`
14. Create `test/test_broker_fundamentals.py`
15. Update `test/test_broker_analytics.py`

### Phase 5: Verification
16. Run fast tests
17. Run full regression
18. Commit and push

---

## FILES TO CREATE

```
market/normalize/upstox_market_info.py   # Holidays, Timings
market/normalize/upstox_margins.py        # Margin calculator
market/normalize/upstox_fundamentals.py   # Company profile, ratios, etc.
test/test_broker_market_info.py
test/test_broker_margins.py
test/test_broker_fundamentals.py
```

## FILES TO MODIFY

```
market/models.py                    # +20 new model classes
app/market_data.py                  # +6 new service methods
api/product_routes.py               # +6 new routes + helpers
test/test_broker_analytics.py       # Extend existing tests
```

---

## ESTIMATED EFFORT

| Phase | Files | Lines | Time |
|-------|-------|-------|------|
| Models | 1 | +150 | 30 min |
| Normalizers | 3 new | +400 | 2 hrs |
| Service layer | 1 | +100 | 30 min |
| API routes | 1 | +150 | 30 min |
| Tests | 3 new | +300 | 1 hr |
| **Total** | **8 files** | **~1100 lines** | **~4 hrs** |

---

## ACCEPTANCE CRITERIA

- [ ] All new models compile and import correctly
- [ ] All normalizers pass unit tests with sample payloads
- [ ] All REST endpoints return correct JSON structure
- [ ] Fast test group passes
- [ ] Full regression passes
- [ ] CI green

---

## NOTES

- Fyers does NOT provide any of these APIs (futures smartlist, margins, FII/DII, fundamentals)
- All new data is Upstox-only; will raise `UnsupportedByProvider` for Fyers
- Models follow existing patterns (frozen dataclasses, validation helpers)
- No MCP tools or WebUI changes per directive
- No trading/order functionality added
