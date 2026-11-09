# Issue 1 + Issue 2 — Canonical Instrument Catalog & F&O Read Model

Status: **in progress** · Started from HEAD `3e9951385dc1286f1ad3adcd1ee28d90e4454740`

Two strict phases. Issue 3 (EventStore connection pooling) is explicitly **out of scope**.

---

## Problem (measured)

The catalog stores provider-native values in columns that read models treat as canonical.

| Row | `tradingsymbol` | `underlying` | `segment` | `provider_symbol` |
|---|---|---|---|---|
| upstox equity | `KRBL` | **`NULL`** | `NSE_EQ` | `NSE_EQ\|INE001B01026` |
| fyers equity | `NSE:BAJJ-AUTO-EQ` | `BAJAJ-AUTO` | **`10`** | `NSE:BAJAJ-AUTO-EQ` |
| fyers index | `NSE:NIFTY50-INDEX` | `NIFTY` | **`10`** | `NSE:NIFTY50-INDEX` |
| fyers future | `NSE:ATHERENERG26SEPFUT` | `ATHERENERG` | **`11`** | `NSE:ATHERENERG26SEPFUT` |

Consequences:
- `equity_universe` (`segment='NSE_EQ'`) → 2,668 rows; **excludes all 3,187 Fyers equities**.
- `fno_universe` joins `eq.tradingsymbol = f.underlying` → works for Upstox, **never** for Fyers
  (`NSE:BAJAJ-AUTO-EQ ≠ BAJAJ-AUTO`) → `fno_universe(provider='fyers') == 0`.

### Key evidence

Fyers `underSym` (stored as `underlying`) **is** the canonical equity symbol, and it matches
Upstox `tradingsymbol` exactly for the same instrument:

```
fyers  NSE:BAJAJ-AUTO-EQ -> BAJAJ-AUTO      upstox BAJAJ-AUTO
fyers  NSE:HCL-INSYS-EQ  -> HCL-INSYS       upstox HCL-INSYS
fyers  NSE:KLBRENG-B-EQ  -> KLBRENG-B       upstox KLBRENG-B
```

Fyers cash rows with empty `underlying`: **0** (100% coverage).

---

## PHASE 1 — canonicalize at ingestion

### Canonical model

| Field | Canonical meaning |
|---|---|
| `segment` | MarketHub segment, **all providers** |
| `tradingsymbol` | canonical/human symbol (cash + derivatives) |
| `provider_symbol` | provider-native identifier, **unchanged** |
| `underlying` | canonical underlying symbol (cash rows populated) |

### Rules (single source of truth, in `core/persistence/modules/products.py`)

1. `canonical_segment(exchange, instrument_type, segment)` — the existing `_derive_segment`
   rule, relocated to the canonical/persistence layer so ingestion **and** the migration
   share one implementation. `app.instruments._derive_segment` delegates to it.
2. `strip_exchange_namespace(symbol)` — removes a leading `EXCH:` prefix.
3. `canonical_cash_symbol(tradingsymbol, underlying)` — authoritative `underlying` when
   present, else the namespace-stripped `tradingsymbol`.

### Per-row application

| Row type | segment | tradingsymbol | underlying |
|---|---|---|---|
| cash (EQUITY/ETF) | canonical | `canonical_cash_symbol(...)` | populated with the canonical symbol |
| derivative (FUTURE/OPTION) | canonical | namespace stripped | preserved (already canonical) |
| INDEX | canonical | **preserved native** (see exception) | preserved |

**INDEX exception (documented, evidence-backed):** `app/market_indices._catalog_row()`
performs an **exact** lookup of the provider symbol (`NSE:NIFTY50-INDEX`) to resolve Fyers
index spots. Canonicalising index `tradingsymbol` would regress index quote resolution.
Provider identity remains fully available in `provider_symbol`.

### Ingestion paths audited

Single funnel — no other write path exists:

```
upstox_master_records()  ─┐
                          ├─→ _filter_by_segments() → replace_provider_instruments()
fyers_master_records()   ─┘
```

### v22 data migration

`migrate_v21_to_v22(conn)` — rewrites `segment`, `tradingsymbol` (cash/derivative),
`underlying` (cash) for existing rows using the **shared** Python rules (not duplicated SQL).
Deterministic, idempotent (second run produces zero updates), bounded, preserves
`provider_symbol`, `provider`, `instrument_token`.

### Gate

```
[ ] ingestion canonicalization implemented
[ ] v22 migration implemented
[ ] migration is idempotent
[ ] Fyers F&O linkage proven
[ ] Upstox behavior preserved
[ ] instrument search/regression green
[ ] representative provider rows validated
[ ] provider budget impact understood
[ ] relevant backend tests green
[ ] no protected architecture modified unnecessarily
```

---

## PHASE 2 — split `fno_universe` enumeration from counts

### Callers (to classify)

- `market/derivatives_universe.stock_underlyings()` — **enumeration** only
- `app/fno_coverage._universe_symbols()` — **enumeration** only
- `api/fno_coverage_routes` / Market Data page — **counts** (via `fno_universe`)

### Design

- `fno_underlying_symbols(...)` — `SELECT DISTINCT f.underlying` + the equity link.
  Smallest possible query; no self-join aggregation, no `COUNT(DISTINCT)`.
- `fno_universe(...)` — unchanged count semantics, kept for count consumers.
- Both cached at the canonical read-model boundary via the existing
  `market/derivatives_universe._cached` helper, keyed `(provider, day, epoch)`.

### Gate

```
[ ] all fno_universe callers classified
[ ] enumeration path separated
[ ] count path separated
[ ] existing semantics preserved
[ ] cache uses existing lifecycle pattern
[ ] cache invalidation/re-keying proven
[ ] Fyers universe works
[ ] Upstox universe remains correct
[ ] relevant performance improvement demonstrated
[ ] full relevant tests green
```

---

## Provider / budget impact (measured, pre-implementation)

| Metric | current | after canonicalisation |
|---|---|---|
| `equity_universe` (`segment='NSE_EQ' AND type='EQUITY'`) | 2,668 | **5,855** |
| `NSE_FO` rows | 36,077 | **117,026** |
| F&O stock underlyings (upstox) | 210 | 210 |
| F&O stock underlyings (fyers) | 0 | (expected > 0) |

Provider budgets: upstox 1800/4500, fyers 4500 (`providers.capabilities.effective_budget`).
The F&O *subscription* universe is the routed selection, not the catalog size — must be
re-verified after Phase 1 rather than assumed.

## Out of scope

- EventStore connection lifecycle / pooling (Issue 3).
- Any protected subsystem: auth, secrets, broker auth/session, broker transport,
  `MarketService` semantics, alert/event delivery, startup lifecycle, subscription
  ownership, frontend, trading.
