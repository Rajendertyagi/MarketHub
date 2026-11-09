// Zod schemas for the contracts consumed by the migrated features.
//
// Validation fails loudly so a backend/frontend mismatch surfaces as a typed
// `parse` error instead of silently producing `undefined` fields downstream.

import { z } from "zod";

export const instrumentTypeSchema = z.enum([
  "EQUITY",
  "INDEX",
  "FUTURE",
  "OPTION",
  "CURRENCY",
  "COMMODITY",
]);

export const instrumentSchema = z.object({
  instrument_token: z.string(),
  exchange: z.string(),
  tradingsymbol: z.string(),
  instrument_type: instrumentTypeSchema,
});

// Compact canonical search row (GET /api/market/search). `type` stays a plain
// string so a future canonical class parses instead of breaking the picker.
export const marketInstrumentSchema = z
  .object({
    instrument_key: z.string(),
    provider: z.string(),
    provider_token: z.string(),
    native_key: z.string().nullable().optional(),
    canonical_id: z.string().nullable().optional(),
    symbol: z.string(),
    display_name: z.string().optional(),
    exchange: z.string(),
    type: z.string(),
    underlying: z.string().nullable().optional(),
    expiry: z.string().nullable().optional(),
    strike: z.number().nullable().optional(),
    option_type: z.enum(["CE", "PE"]).nullable().optional(),
    lot_size: z.number().nullable().optional(),
  })
  .passthrough();

export const marketSearchResponseSchema = z.object({
  query: z.string(),
  parsed: z.record(z.string(), z.unknown()).optional(),
  count: z.number(),
  results: z.array(marketInstrumentSchema),
});

export const browseResponseSchema = z.object({
  count: z.number(),
  results: z.array(marketInstrumentSchema),
  next_cursor: z.string().nullable(),
  total: z.number().nullable(),
});

// Curated Vela native-picker pool (GET /api/instruments/pool). Carries Vela
// SymbolDescriptor fields plus the exact canonical identity so a native-picker
// selection resolves to the correct provider/token (never a same-ticker collision).
export const poolSymbolSchema = z
  .object({
    ticker: z.string(),
    description: z.string().optional(),
    type: z.string().optional(),
    group: z.string().optional(),
    prefix: z.string().optional(),
    provider: z.string().optional(),
    provider_token: z.string().optional(),
    instrument_key: z.string().optional(),
    native_key: z.string().nullable().optional(),
    canonical_id: z.string().nullable().optional(),
    exchange: z.string().optional(),
    instrument_type: z.string().optional(),
    symbol: z.string().optional(),
  })
  .passthrough();

export const poolResponseSchema = z.object({
  count: z.number(),
  symbols: z.array(poolSymbolSchema),
});

export const candleSchema = z.object({
  timestamp: z.string(),
  open: z.number(),
  high: z.number(),
  low: z.number(),
  close: z.number(),
  volume: z.number().nullable().optional(),
  open_interest: z.number().nullable().optional(),
});

export const historyResponseSchema = z.object({
  candles: z.array(candleSchema),
});

export const marketBarSchema = z.object({
  ts: z.number(),
  open: z.number(),
  high: z.number(),
  low: z.number(),
  close: z.number(),
  volume: z.number().nullable(),
  oi: z.number().nullable(),
  // Null for derived (week/month) and live-merged bars, which have no single
  // stored provenance.
  source: z.string().nullable(),
});

export const barsResponseSchema = z.object({
  provider: z.string(),
  instrument_key: z.string(),
  timeframe: z.number(),
  bars: z.array(marketBarSchema),
});

export const scannerDefSchema = z.object({
  name: z.string(),
  title: z.string(),
  instrument_class: z.string(),
  metric: z.string(),
  order: z.string(),
  contract_kind: z.enum(["future", "option"]).nullable().optional(),
  extra: z.array(z.string()).nullable().optional(),
  description: z.string(),
});

export const scannersListSchema = z.object({
  status: z.string(),
  scanners: z.array(scannerDefSchema),
});

export const scanRowSchema = z.object({
  symbol: z.string(),
  sector: z.string(),
  ltp: z.number().nullable().optional(),
  change: z.number().nullable().optional(),
  change_percent: z.number().nullable().optional(),
  volume: z.union([z.number(), z.null()]).optional(),
  oi: z.union([z.number(), z.null()]).optional(),
  iv: z.number().nullable().optional(),
  status: z.string(),
  freshness: z.string().nullable().optional(),
  contract: z.string().nullable().optional(),
  expiry: z.string().nullable().optional(),
  option_type: z.string().nullable().optional(),
  strike: z.number().nullable().optional(),
  oi_change: z.union([z.number(), z.null()]).optional(),
  oi_change_percent: z.number().nullable().optional(),
});

export const scanResultSchema = z.object({
  scanner: z.string(),
  universe: z.string(),
  as_of: z.string().nullable().optional(),
  eligible: z.number(),
  quoted: z.number(),
  matched: z.number(),
  rows: z.array(scanRowSchema),
});

// ── Market Breadth (GET /api/market/breadth) ────────────────────────────────
export const breadthRowSchema = z.object({
  symbol: z.string(),
  name: z.string().nullable(),
  exchange: z.string(),
  instrument_token: z.string().nullable(),
  ltp: z.number().nullable(),
  change: z.number().nullable(),
  change_percent: z.number().nullable(),
  status: z.string(),
  sector: z.string(),
  received_ts: z.string().nullable(),
});

export const breadthSchema = z.object({
  universe: z.string(),
  eligible: z.number(),
  quoted: z.number(),
  unavailable: z.number(),
  advances: z.number(),
  declines: z.number(),
  unchanged: z.number(),
  advance_percent: z.number(),
  decline_percent: z.number(),
  ad_ratio: z.number().nullable(),
  net_advances: z.number(),
  as_of: z.string().nullable(),
  unclassified: z.number(),
  stale: z.boolean(),
  volume_advancing: z.number().nullable(),
  volume_declining: z.number().nullable(),
  intraday_highs: z.number().nullable(),
  intraday_lows: z.number().nullable(),
  weighting: z.string(),
  rows: z.array(breadthRowSchema),
});

// ── Sector Heatmap (GET /api/market/sector-heatmap) ─────────────────────────
export const sectorMemberSchema = z.object({
  symbol: z.string(),
  name: z.string().nullable(),
  ltp: z.number().nullable(),
  change: z.number().nullable(),
  change_percent: z.number().nullable(),
  volume: z.number().nullable(),
  status: z.string(),
  fno: z.boolean(),
});

export const sectorRowSchema = z.object({
  sector: z.string(),
  constituent_count: z.number(),
  quoted: z.number(),
  unavailable: z.number(),
  advances: z.number(),
  declines: z.number(),
  unchanged: z.number(),
  average_change_percent: z.number().nullable(),
  median_change_percent: z.number().nullable(),
  net_advances: z.number(),
  top_gainer: z.record(z.string(), z.unknown()).nullable(),
  top_loser: z.record(z.string(), z.unknown()).nullable(),
  weighting: z.string(),
  members: z.array(sectorMemberSchema),
});

export const sectorHeatmapSchema = z.object({
  universe: z.string(),
  sector_count: z.number(),
  classified_count: z.number(),
  unclassified_count: z.number(),
  eligible: z.number(),
  quoted: z.number(),
  unavailable: z.number(),
  advances: z.number(),
  declines: z.number(),
  unchanged: z.number(),
  as_of: z.string().nullable(),
  weighting: z.string(),
  stale: z.boolean(),
  sectors: z.array(sectorRowSchema),
  reconciliation: z.record(z.string(), z.unknown()),
});

// ── Market Map (GET /api/market/map) ────────────────────────────────────────
export const mapStockSchema = z.object({
  symbol: z.string(),
  exchange: z.string(),
  instrument_token: z.string().nullable(),
  sector: z.string(),
  ltp: z.number().nullable(),
  change: z.number().nullable(),
  change_percent: z.number().nullable(),
  volume: z.number().nullable(),
  status: z.string(),
  fno: z.boolean(),
  received_ts: z.string().nullable(),
});

export const marketMapSectorSchema = z.object({
  sector: z.string(),
  stocks: z.array(mapStockSchema),
  advances: z.number(),
  declines: z.number(),
  unchanged: z.number(),
  unavailable: z.number(),
  quoted: z.number(),
});

export const marketMapSchema = z.object({
  universe: z.string(),
  eligible: z.number(),
  quoted: z.number(),
  unavailable: z.number(),
  advances: z.number(),
  declines: z.number(),
  unchanged: z.number(),
  unclassified: z.number(),
  as_of: z.string().nullable(),
  stale: z.boolean(),
  sectors: z.array(marketMapSectorSchema),
  reconciliation: z.record(z.string(), z.unknown()),
});

export type HistoryResponseParsed = z.infer<typeof historyResponseSchema>;
export type ScannerDefParsed = z.infer<typeof scannerDefSchema>;
export type ScanRowParsed = z.infer<typeof scanRowSchema>;
export type ScanResultParsed = z.infer<typeof scanResultSchema>;
export type BreadthSnapshotParsed = z.infer<typeof breadthSchema>;
export type SectorHeatmapSnapshotParsed = z.infer<typeof sectorHeatmapSchema>;
export type MarketMapSnapshotParsed = z.infer<typeof marketMapSchema>;

// ── F&O Universe (GET /api/market/fno/universe) ────────────────────────────────
export const fnoUniverseRowSchema = z.object({
  symbol: z.string(),
  name: z.string().nullable(),
  equity_key: z.string().nullable(),
  futures_available: z.boolean(),
  options_available: z.boolean(),
  futures_count: z.number(),
  options_count: z.number(),
  future_expiries: z.number(),
  option_expiries: z.number(),
  nearest_future: z.string().nullable(),
  nearest_option: z.string().nullable(),
});

export const fnoUniverseSchema = z.object({
  status: z.string(),
  count: z.number(),
  universe: z.array(fnoUniverseRowSchema),
});

// ── Shared quote projection ────────────────────────────────────────────────────
export const quoteSchema = z
  .object({
    ltp: z.number().nullable().optional(),
    change: z.number().nullable().optional(),
    change_percent: z.number().nullable().optional(),
    open: z.number().nullable().optional(),
    high: z.number().nullable().optional(),
    low: z.number().nullable().optional(),
    volume: z.number().nullable().optional(),
    open_interest: z.number().nullable().optional(),
    oi_change: z.number().nullable().optional(),
    previous_oi: z.number().nullable().optional(),
    best_bid: z.number().nullable().optional(),
    best_ask: z.number().nullable().optional(),
    bid: z.number().nullable().optional(),
    ask: z.number().nullable().optional(),
    iv: z.number().nullable().optional(),
    delta: z.number().nullable().optional(),
    gamma: z.number().nullable().optional(),
    theta: z.number().nullable().optional(),
    vega: z.number().nullable().optional(),
    rho: z.number().nullable().optional(),
    received_ts: z.string().nullable().optional(),
    received_at: z.string().nullable().optional(),
  })
  .passthrough();

export const fnoFutureSchema = z.object({
  key: z.string(),
  label: z.string(),
  expiry: z.string(),
  provider: z.string().nullable(),
  quote: quoteSchema.nullable().optional(),
});

export const fnoOptionSchema = z.object({
  key: z.string(),
  label: z.string(),
  expiry: z.string(),
  strike: z.number(),
  option_type: z.enum(["CE", "PE"]),
  provider: z.string().nullable(),
  quote: quoteSchema.nullable().optional(),
});

export const fnoWorkspaceSchema = z.object({
  status: z.string(),
  symbol: z.string(),
  equity_key: z.string().nullable(),
  spot_quote: quoteSchema.nullable().optional(),
  futures: z.array(fnoFutureSchema),
  options: z.array(fnoOptionSchema),
  option_expiries: z.array(z.string()),
  selected_expiry: z.string().nullable(),
  atm: z.number().nullable(),
  atm_basis: z.string().nullable().optional(),
  notes: z.array(z.string()).optional(),
});

export const fnoViewSchema = z.object({
  status: z.string(),
  symbol: z.string(),
  active_view: z.record(z.string(), z.number()),
  resolved_count: z.number(),
  apply: z.record(z.string(), z.unknown()),
});

// ── Option Chain view (GET /api/options/chain/view) ───────────────────────────
export const optionLegSchema = z.object({
  instrument_key: z.string(),
  symbol: z.string(),
  exchange: z.string(),
  option_type: z.enum(["CE", "PE"]),
  strike: z.number(),
  expiry: z.string().nullable().optional(),
  quote: quoteSchema.nullable().optional(),
});

export const chainRowSchema = z.object({
  strike: z.number(),
  atm: z.boolean(),
  call: optionLegSchema.nullable().optional(),
  put: optionLegSchema.nullable().optional(),
});

export const chainAnalyticsSchema = z
  .object({
    scope: z.string().optional(),
    total_call_oi: z.number().nullable().optional(),
    total_put_oi: z.number().nullable().optional(),
    pcr_by_oi: z.number().nullable().optional(),
    highest_call_oi_strike: z.number().nullable().optional(),
    highest_put_oi_strike: z.number().nullable().optional(),
  })
  .passthrough();

export const optionChainViewSchema = z.object({
  underlying: z.string(),
  underlying_instrument: z.record(z.string(), z.unknown()).nullable().optional(),
  expiry: z.string(),
  expiries_available: z.array(z.string()),
  spot: z.number().nullable(),
  spot_basis: z.string().nullable(),
  atm_strike: z.number().nullable(),
  window: z.number(),
  strikes_loaded: z.number(),
  strikes_total_listed: z.number(),
  rows: z.array(chainRowSchema),
  analytics: chainAnalyticsSchema,
});

export const optionUnderlyingsSchema = z.object({
  underlyings: z.array(z.string()),
});

export const optionExpiriesSchema = z.object({
  underlying: z.string(),
  expiries: z.array(z.string()),
});

// Canonical index option-chain underlyings (backend-owned; the frontend no
// longer hard-codes this list). Human/canonical labels + exchange only.
export const indexOptionUnderlyingsSchema = z.object({
  underlyings: z.array(
    z.object({
      label: z.string(),
      exchange: z.string(),
    }),
  ),
});

export const futureContractSchema = z
  .object({
    instrument_key: z.string(),
    symbol: z.string(),
    exchange: z.string(),
    expiry: z.string().nullable().optional(),
    type: z.string().nullable().optional(),
    ltp: z.number().nullable().optional(),
  })
  .passthrough();

export const futuresResponseSchema = z.object({
  underlying: z.string(),
  underlying_instrument: z.record(z.string(), z.unknown()).nullable().optional(),
  expiries: z.array(z.string()).optional(),
  contracts: z.array(futureContractSchema),
});

export type FnoUniverseParsed = z.infer<typeof fnoUniverseSchema>;
export type FnoWorkspaceParsed = z.infer<typeof fnoWorkspaceSchema>;
export type OptionChainViewParsed = z.infer<typeof optionChainViewSchema>;
export type FuturesResponseParsed = z.infer<typeof futuresResponseSchema>;

// ── Subscriptions (GET /api/subscriptions) ────────────────────────────────────
export const subscriptionIndexSchema = z.object({
  label: z.string(),
  key: z.string(),
  fyers_symbol: z.string(),
  // Additive canonical identity (S8a). Optional so a payload without it
  // still parses (backwards compatible); the UI falls back to `key`.
  canonical_id: z.string().nullable().optional(),
  enabled: z.boolean(),
  canonical: z.boolean(),
});

export const subscriptionStockSchema = z.object({
  key: z.string(),
  label: z.string(),
  canonical_id: z.string().nullable().optional(),
  enabled: z.boolean(),
});

export const derivativeRuleSchema = z.object({
  underlying: z.string(),
  futures_enabled: z.boolean(),
  futures_count: z.number(),
  options_enabled: z.boolean(),
  options_count: z.number(),
  strikes_below: z.number(),
  strikes_above: z.number(),
  calls_enabled: z.boolean(),
  puts_enabled: z.boolean(),
  updated_at: z.string().nullable().optional(),
});

export const subscriptionPreferencesSchema = z.object({
  status: z.string().optional(),
  indices: z.array(subscriptionIndexSchema),
  stocks: z.array(subscriptionStockSchema),
  derivatives: z.array(derivativeRuleSchema),
});

export const applyResultSchema = z
  .object({
    status: z.string(),
    resolved_count: z.number().optional(),
    by_provider: z.record(z.string(), z.number()).optional(),
    apply: z.unknown().optional(),
  })
  .passthrough();

// ── Instruments / catalog (GET /api/instruments/segments, /sync) ─────────────
export const segmentInfoSchema = z.object({
  segment: z.string(),
  enabled: z.boolean(),
  default: z.boolean(),
  catalog_rows: z.number(),
});

export const segmentsResponseSchema = z.object({
  status: z.string(),
  default: z.array(z.string()),
  enabled: z.array(z.string()),
  segments: z.array(segmentInfoSchema),
});

export const syncStateSchema = z
  .object({
    providers: z.array(z.record(z.string(), z.unknown())),
  })
  .passthrough();

// ── F&O coverage / Futures Board (/api/futures/heatmap, /api/fno/*) ──────────

// Runtime validation for the F&O coverage + futures-board contracts. Kept
// permissive where the backend owns the shape (`.passthrough()`, nullable /
// optional numerics) so a backend field addition never breaks the UI, while a
// genuinely wrong type still surfaces as a typed parse error.

export const fnoProviderInfoSchema = z
  .object({
    provider: z.string(),
    label: z.string().nullable().optional(),
    enabled: z.boolean().nullable().optional(),
    status: z.string().nullable().optional(),
    available: z.boolean().nullable().optional(),
    capabilities: z.array(z.string()).nullable().optional(),
    budget: z.number().nullable().optional(),
  })
  .passthrough();

export const fnoCapacitySchema = z
  .object({
    budget: z.number().nullable().optional(),
    used: z.number().nullable().optional(),
    available: z.number().nullable().optional(),
  })
  .passthrough();

export const fnoRoutedContractSchema = z
  .object({
    underlying: z.string(),
    kind: z.string(),
    exchange: z.string(),
    expiry: z.string(),
    instrument_type: z.string(),
    provider: z.string(),
    provider_token: z.string().nullable(),
    tradingsymbol: z.string().nullable().optional(),
    lot_size: z.number().nullable().optional(),
    state: z.string(),
    reason: z.string().nullable().optional(),
  })
  .passthrough();

export const fnoCoverageSchema = z
  .object({
    status: z.string().optional(),
    enabled: z.boolean(),
    expiry_requested: z.string(),
    expiry_resolved: z.string().nullable(),
    expiries: z.array(z.string()),
    desired: z.number(),
    routed: z.number(),
    by_provider: z.record(z.string(), z.number()),
    states: z.record(z.string(), z.number()),
    capacity: z.record(z.string(), fnoCapacitySchema),
    providers: z.array(fnoProviderInfoSchema),
    uncovered: z.array(fnoRoutedContractSchema),
    errors: z.array(z.string()),
    contracts: z.array(fnoRoutedContractSchema).optional(),
  })
  .passthrough();

// The heatmap's coverage block mirrors the plan but reports `uncovered` as a
// COUNT, not a contract list.
export const futuresBoardCoverageSchema = z
  .object({
    desired: z.number(),
    routed: z.number(),
    by_provider: z.record(z.string(), z.number()),
    states: z.record(z.string(), z.number()),
    capacity: z.record(z.string(), fnoCapacitySchema),
    uncovered: z.number(),
    errors: z.array(z.string()),
  })
  .passthrough();

export const futuresHeatTileSchema = z
  .object({
    symbol: z.string(),
    kind: z.string(),
    exchange: z.string(),
    underlying: z.string(),
    expiry: z.string(),
    provider: z.string().nullable(),
    instrument_key: z.string(),
    futures_ltp: z.number().nullable(),
    price_pct: z.number().nullable(),
    oi: z.number().nullable(),
    previous_oi: z.number().nullable(),
    oi_change: z.number().nullable(),
    oi_pct: z.number().nullable(),
    buildup: z.string().nullable(),
    spot_ltp: z.number().nullable(),
    basis: z.number().nullable(),
    coverage: z.string().nullable(),
    oi_basis: z.string().nullable(),
    received_ts: z.string().nullable(),
    status: z.string().optional(),
  })
  .passthrough();

export const futuresHeatmapSchema = z
  .object({
    expiry_requested: z.string(),
    expiry_resolved: z.string().nullable(),
    expiries: z.array(z.string()),
    eligible: z.number(),
    quoted: z.number(),
    unavailable: z.number(),
    class_counts: z.record(z.string(), z.number()),
    as_of: z.string().nullable(),
    data_status: z.string(),
    items: z.array(futuresHeatTileSchema),
    buildup: z.string(),
    providers: z.array(fnoProviderInfoSchema),
    coverage: futuresBoardCoverageSchema,
  })
  .passthrough();

export const fnoCoverageUniverseSchema = z
  .object({
    status: z.string(),
    enabled: z.boolean(),
    indices: z.array(z.string()),
    stocks: z.array(z.string()),
    expiries: z.array(z.string()),
    expiry_resolved: z.string().nullable(),
    errors: z.array(z.string()),
  })
  .passthrough();

export const fnoApplySchema = z
  .object({
    status: z.string(),
    expiry_resolved: z.string().nullable().optional(),
    desired: z.number().optional(),
    routed: z.number().optional(),
    by_provider: z.record(z.string(), z.number()).optional(),
    apply: z.unknown().optional(),
    released: z.boolean().optional(),
  })
  .passthrough();
