// MarketHub frontend — shared TypeScript contracts.
//
// These mirror the existing backend REST/SSE contracts. They intentionally do
// NOT introduce a second frontend domain model; field names and semantics
// follow the canonical services (market/scanner.py, market/serialization.py).
//
// Canonical invariants preserved here:
//  - Instrument identity is exact (instrument_token + exchange + tradingsymbol).
//    We never fabricate a token such as NSE_EQ|SYMBOL.
//  - IV is a canonical decimal fraction (0.1758 == 17.58%). Only presentation
//    converts it to a percentage.

export type InstrumentType =
  | "EQUITY"
  | "INDEX"
  | "ETF"
  | "FUTURE"
  | "OPTION"
  | "CURRENCY"
  | "COMMODITY";

export interface Instrument {
  instrument_token: string;
  exchange: string;
  tradingsymbol: string;
  instrument_type: InstrumentType;
  /** Catalogue provider that owns `instrument_token` (additive). */
  provider?: string;
}

// Compact canonical identity returned by GET /api/market/search (and its
// `exact=1` mode). The backend dedupes providers, so one row is one real
// instrument. `provider_token` is the BARE token the bars API expects;
// `instrument_key` is the composite EXCHANGE:TOKEN display key and must never
// be sent to /market/bars.
export interface MarketInstrument {
  instrument_key: string;
  provider: string;
  provider_token: string;
  native_key?: string | null;
  canonical_id?: string | null;
  symbol: string;
  display_name?: string;
  exchange: string;
  type: InstrumentType;
  underlying?: string | null;
  expiry?: string | null;
  strike?: number | null;
  option_type?: "CE" | "PE" | null;
  lot_size?: number | null;
}

// Paginated canonical browse (GET /api/instruments/browse, Phase 1). The cursor
// is opaque and bound to the filter set; `total` is null on an empty first page
// and on continuation pages (backend limitation L1).
export interface BrowseResponse {
  count: number;
  results: MarketInstrument[];
  next_cursor: string | null;
  total: number | null;
}

// Curated Vela native-picker pool (GET /api/instruments/pool). Vela descriptor
// fields + exact canonical identity for safe chart resolution.
export interface PoolSymbol {
  ticker: string;
  description?: string;
  type?: string;
  group?: string;
  prefix?: string;
  provider?: string;
  provider_token?: string;
  instrument_key?: string;
  native_key?: string | null;
  canonical_id?: string | null;
  exchange?: string;
  instrument_type?: string;
  symbol?: string;
}

export interface PoolResponse {
  count: number;
  symbols: PoolSymbol[];
}

export interface Candle {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number | null;
  open_interest?: number | null;
}

export interface HistoryResponse {
  candles: Candle[];
}

// Persisted OHLCV bar (GET /api/market/bars). `ts` is the bar OPEN time in
// epoch milliseconds; `source` records provenance (live feed vs backfill) and
// is null for derived (week/month) and live-merged bars.
export interface MarketBar {
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  oi: number | null;
  source: string | null;
}

export interface BarsResponse {
  provider: string;
  instrument_key: string;
  timeframe: number;
  bars: MarketBar[];
}

// Scanner metadata returned by GET /api/market/scanners.
export interface ScannerDef {
  name: string;
  title: string;
  instrument_class: string;
  metric: string;
  order: string;
  contract_kind?: "future" | "option" | null;
  extra?: string[] | null;
  description: string;
}

export interface ScannersListResponse {
  status: string;
  scanners: ScannerDef[];
}

// A single scanner result row. Field set is the union across equity / future /
// option instrument classes; per-class columns are selected at render time.
export interface ScanRow {
  symbol: string;
  sector: string;
  ltp: number | null;
  change: number | null;
  change_percent: number | null;
  volume: number | null;
  oi: number | null;
  iv: number | null;
  status: string;
  freshness: string | null;
  contract?: string | null;
  expiry?: string | null;
  option_type?: string | null;
  strike?: number | null;
  oi_change?: number | null;
  oi_change_percent?: number | null;
}

export interface ScanResult {
  scanner: string;
  universe: string;
  as_of: string | null;
  eligible: number;
  quoted: number;
  matched: number;
  rows: ScanRow[];
}

// Parameters accepted by GET /api/market/scanner/{name}.
export interface ScannerRunParams {
  universe: string;
  limit: number;
  expiry?: string;
  atm_range?: number;
  option_type?: "CE" | "PE" | "BOTH";
}

// ── Market analytics universes ───────────────────────────────────────────────
// The single canonical universe set (market/market_universe.UNIVERSE_NAMES),
// which is also the only set accepted by the analytics-coverage owner. The
// frontend never invents a divergent universe model.
export const ANALYTICS_UNIVERSES = ["FNO", "NSE_EQ", "NIFTY50", "NIFTYNXT50", "BANKNIFTY"] as const;

export type AnalyticsUniverse = (typeof ANALYTICS_UNIVERSES)[number];

// ── Market Breadth (GET /api/market/breadth) ────────────────────────────────
export type BreadthStatus = "advance" | "decline" | "unchanged" | "unavailable";

export interface BreadthRow {
  symbol: string;
  name: string | null;
  exchange: string;
  instrument_token: string | null;
  ltp: number | null;
  change: number | null;
  change_percent: number | null;
  status: BreadthStatus;
  sector: string;
  received_ts: string | null;
}

export interface BreadthSnapshot {
  universe: string;
  eligible: number;
  quoted: number;
  unavailable: number;
  advances: number;
  declines: number;
  unchanged: number;
  advance_percent: number;
  decline_percent: number;
  ad_ratio: number | null;
  net_advances: number;
  as_of: string | null;
  unclassified: number;
  stale: boolean;
  volume_advancing: number | null;
  volume_declining: number | null;
  intraday_highs: number | null;
  intraday_lows: number | null;
  weighting: string;
  rows: BreadthRow[];
}

// ── Sector Heatmap (GET /api/market/sector-heatmap) ─────────────────────────
export interface SectorMember {
  symbol: string;
  name: string | null;
  ltp: number | null;
  change: number | null;
  change_percent: number | null;
  volume: number | null;
  status: string;
  fno: boolean;
}

export interface SectorRow {
  sector: string;
  constituent_count: number;
  quoted: number;
  unavailable: number;
  advances: number;
  declines: number;
  unchanged: number;
  average_change_percent: number | null;
  median_change_percent: number | null;
  net_advances: number;
  top_gainer: Record<string, unknown> | null;
  top_loser: Record<string, unknown> | null;
  weighting: string;
  members: SectorMember[];
}

export interface SectorHeatmapSnapshot {
  universe: string;
  sector_count: number;
  classified_count: number;
  unclassified_count: number;
  eligible: number;
  quoted: number;
  unavailable: number;
  advances: number;
  declines: number;
  unchanged: number;
  as_of: string | null;
  weighting: string;
  stale: boolean;
  sectors: SectorRow[];
  reconciliation: Record<string, unknown>;
}

// ── Market Map (GET /api/market/map) ────────────────────────────────────────
export interface MapStock {
  symbol: string;
  exchange: string;
  instrument_token: string | null;
  sector: string;
  ltp: number | null;
  change: number | null;
  change_percent: number | null;
  volume: number | null;
  status: string;
  fno: boolean;
  received_ts: string | null;
}

export interface MarketMapSector {
  sector: string;
  stocks: MapStock[];
  advances: number;
  declines: number;
  unchanged: number;
  unavailable: number;
  quoted: number;
}

export interface MarketMapSnapshot {
  universe: string;
  eligible: number;
  quoted: number;
  unavailable: number;
  advances: number;
  declines: number;
  unchanged: number;
  unclassified: number;
  as_of: string | null;
  stale: boolean;
  sectors: MarketMapSector[];
  reconciliation: Record<string, unknown>;
}

// ── F&O Universe (GET /api/market/fno/universe) ──────────────────────────────
export interface FnoUniverseRow {
  symbol: string;
  name: string | null;
  equity_key: string | null;
  futures_available: boolean;
  options_available: boolean;
  futures_count: number;
  options_count: number;
  future_expiries: number;
  option_expiries: number;
  nearest_future: string | null;
  nearest_option: string | null;
}

export interface FnoUniverseResponse {
  status: string;
  count: number;
  universe: FnoUniverseRow[];
}

// ── Shared quote projection (canonical; fields optional, never fabricated) ─────
// Backend quote shapes vary by endpoint (spot/future quotes expose best_bid/
// best_ask; option quotes expose bid/ask + greeks/iv). We model the union and
// read defensively (bid ?? best_bid) so no field is coerced to a fake value.
export interface Quote {
  ltp?: number | null;
  change?: number | null;
  change_percent?: number | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  volume?: number | null;
  open_interest?: number | null;
  oi_change?: number | null;
  previous_oi?: number | null;
  best_bid?: number | null;
  best_ask?: number | null;
  bid?: number | null;
  ask?: number | null;
  iv?: number | null;
  delta?: number | null;
  gamma?: number | null;
  theta?: number | null;
  vega?: number | null;
  rho?: number | null;
  received_ts?: string | null;
  received_at?: string | null;
}

// ── F&O Workspace stock (GET /api/market/fno/stock/{symbol}) ──────────────────
export interface FnoFuture {
  key: string;
  label: string;
  expiry: string;
  provider: string | null;
  quote?: Quote | null;
}

export interface FnoOption {
  key: string;
  label: string;
  expiry: string;
  strike: number;
  option_type: "CE" | "PE";
  provider: string | null;
  quote?: Quote | null;
}

export interface FnoWorkspace {
  status: string;
  symbol: string;
  equity_key: string | null;
  spot_quote?: Quote | null;
  futures: FnoFuture[];
  options: FnoOption[];
  option_expiries: string[];
  selected_expiry: string | null;
  atm: number | null;
  atm_basis?: string | null;
  notes?: string[];
}

// ── F&O Active View (POST /api/market/fno/view) ──────────────────────────────
export interface FnoViewResponse {
  status: string;
  symbol: string;
  active_view: Record<string, number>;
  resolved_count: number;
  apply: Record<string, unknown>;
}

// ── Option Chain view (GET /api/options/chain/view) ───────────────────────────
export interface OptionLeg {
  instrument_key: string;
  symbol: string;
  exchange: string;
  option_type: "CE" | "PE";
  strike: number;
  expiry?: string | null;
  quote?: Quote | null;
}

export interface ChainRow {
  strike: number;
  atm: boolean;
  call?: OptionLeg | null;
  put?: OptionLeg | null;
}

export interface ChainAnalytics {
  scope?: string;
  total_call_oi?: number | null;
  total_put_oi?: number | null;
  pcr_by_oi?: number | null;
  highest_call_oi_strike?: number | null;
  highest_put_oi_strike?: number | null;
  [key: string]: unknown;
}

export interface OptionChainView {
  underlying: string;
  underlying_instrument?: Record<string, unknown> | null;
  expiry: string;
  expiries_available: string[];
  spot: number | null;
  spot_basis: string | null;
  atm_strike: number | null;
  window: number;
  strikes_loaded: number;
  strikes_total_listed: number;
  rows: ChainRow[];
  analytics: ChainAnalytics;
}

export interface OptionExpiriesResponse {
  underlying: string;
  expiries: string[];
}

// Canonical index option-chain underlying (backend-owned; see /api/options/
// index-underlyings). The frontend consumes this instead of hard-coding the
// supported index list.
export interface IndexOptionUnderlying {
  label: string;
  exchange: string;
}

// ── Futures by underlying (GET /api/futures) ─────────────────────────────────
export interface FutureContract {
  instrument_key: string;
  symbol: string;
  exchange: string;
  expiry?: string | null;
  type?: string | null;
  ltp?: number | null;
  [key: string]: unknown;
}

export interface FuturesResponse {
  underlying: string;
  underlying_instrument?: Record<string, unknown> | null;
  expiries?: string[];
  contracts: FutureContract[];
}

// ── Normalized view models (presentation-only) ────────────────────────────────
export type FnoUnderlyingKind = "equity" | "index";

export interface FnoUnderlying {
  symbol: string;
  name: string | null;
  kind: FnoUnderlyingKind;
}

export interface ChainLegView {
  key: string;
  label: string;
  exchange: string;
  option_type: "CE" | "PE";
  strike: number;
  quote?: Quote | null;
}

export interface ChainRowView {
  strike: number;
  atm: boolean;
  call?: ChainLegView;
  put?: ChainLegView;
}

export interface FutureView {
  key: string;
  label: string;
  exchange: string;
  expiry: string;
  quote?: Quote | null;
}

// Typed error taxonomy produced by the API client (see api/client.ts).
export type ApiErrorKind = "network" | "http" | "parse" | "abort" | "unknown";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;
  readonly payload?: unknown;

  constructor(
    kind: ApiErrorKind,
    message: string,
    options?: { status?: number; payload?: unknown },
  ) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = options?.status;
    this.payload = options?.payload;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  get isUnsupported(): boolean {
    return /unsupport|not (available|supported)/i.test(this.message);
  }
}

// Discriminated async state used by views/hooks (avoids ad-hoc booleans).
export type AsyncState<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  | { status: "success"; data: T };

// ── Subscriptions (GET /api/subscriptions) ───────────────────────────────────
// DB-backed preference projection. The DB remains the source of truth; React is
// only the control surface. Canonical index identities come from the backend
// (app.market_indices.MAJOR_INDICES) — never re-defined in the frontend.
export interface SubscriptionIndex {
  label: string;
  key: string;
  fyers_symbol: string;
  canonical_id?: string | null;
  enabled: boolean;
  canonical: boolean;
}

export interface SubscriptionStock {
  key: string;
  label: string;
  canonical_id?: string;
  enabled: boolean;
}

// Derivative rule: the frontend is only the editor. The backend resolves the
// actual current contracts (current/next expiry, ATM ± N, CE/PE) — the
// frontend never rolls expiries or discovers strikes client-side.
export interface DerivativeRule {
  underlying: string;
  futures_enabled: boolean;
  futures_count: number;
  options_enabled: boolean;
  options_count: number;
  strikes_below: number;
  strikes_above: number;
  calls_enabled: boolean;
  puts_enabled: boolean;
  updated_at?: string | null;
}

export interface SubscriptionPreferences {
  indices: SubscriptionIndex[];
  stocks: SubscriptionStock[];
  derivatives: DerivativeRule[];
}

export interface ApplyResult {
  status: string;
  resolved_count?: number;
  by_provider?: Record<string, number>;
  apply?: unknown;
}

// ── Instruments / catalog (GET /api/instruments/segments, /sync) ─────────────
export interface SegmentInfo {
  segment: string;
  enabled: boolean;
  default: boolean;
  catalog_rows: number;
}

export interface SegmentsResponse {
  status: string;
  default: string[];
  enabled: string[];
  segments: SegmentInfo[];
}

export interface SyncProviderState {
  provider: string;
  status?: string;
  last_sync?: string | null;
  rows?: number;
  error?: string | null;
}

export interface SyncResult {
  status: string;
  [key: string]: unknown;
}

// ── F&O coverage / Futures Board (GET /api/futures/heatmap, /api/fno/*) ──────

/** One routed contract from FnoCoverageService (RoutingPlan contract entry). */
export interface FnoRoutedContract {
  underlying: string;
  kind: string;
  exchange: string;
  expiry: string;
  instrument_type: string;
  provider: string;
  provider_token: string | null;
  tradingsymbol: string | null;
  lot_size: number | null;
  /** market.coverage_states — the UI never invents a state. */
  state: string;
  reason?: string | null;
}

/** Declared provider capability/budget row (providers.registry). */
export interface FnoProviderInfo {
  provider: string;
  label?: string;
  enabled?: boolean;
  status?: string | null;
  available?: boolean;
  capabilities?: string[];
  budget?: number | null;
}

export interface FnoProviderCapacity {
  budget?: number;
  used?: number;
  available?: number;
  [key: string]: unknown;
}

/** GET /api/fno/coverage — the whole RoutingPlan projection. */
export interface FnoCoverageSnapshot {
  status?: string;
  enabled: boolean;
  expiry_requested: string;
  expiry_resolved: string | null;
  expiries: string[];
  desired: number;
  routed: number;
  by_provider: Record<string, number>;
  states: Record<string, number>;
  capacity: Record<string, FnoProviderCapacity>;
  providers: FnoProviderInfo[];
  uncovered: FnoRoutedContract[];
  errors: string[];
  contracts?: FnoRoutedContract[];
}

/** Coverage block attached to the heatmap payload (`uncovered` is a COUNT there). */
export interface FuturesBoardCoverage {
  desired: number;
  routed: number;
  by_provider: Record<string, number>;
  states: Record<string, number>;
  capacity: Record<string, FnoProviderCapacity>;
  uncovered: number;
  errors: string[];
}

/** One tile of the futures board wall (market.futures_board.FuturesTile). */
export interface FuturesHeatTile {
  symbol: string;
  kind: string;
  exchange: string;
  underlying: string;
  expiry: string;
  provider: string | null;
  instrument_key: string;
  futures_ltp: number | null;
  price_pct: number | null;
  oi: number | null;
  previous_oi: number | null;
  oi_change: number | null;
  oi_pct: number | null;
  buildup: string | null;
  spot_ltp: number | null;
  basis: number | null;
  /** market.coverage_states — never a UI-invented value. */
  coverage: string | null;
  oi_basis: string | null;
  received_ts: string | null;
  status?: string;
}

/** GET /api/futures/heatmap — board snapshot + route-level additions. */
export interface FuturesHeatSnapshot {
  expiry_requested: string;
  expiry_resolved: string | null;
  expiries: string[];
  eligible: number;
  quoted: number;
  unavailable: number;
  class_counts: Record<string, number>;
  as_of: string | null;
  data_status: string;
  items: FuturesHeatTile[];
  buildup: string;
  providers: FnoProviderInfo[];
  coverage: FuturesBoardCoverage;
}

export interface FnoApplyResult {
  status: string;
  expiry_resolved?: string | null;
  desired?: number;
  routed?: number;
  by_provider?: Record<string, number>;
  apply?: unknown;
  released?: boolean;
}

/** GET /api/fno/universe — routed canonical F&O universe (enabled + lists). */
export interface FnoCoverageUniverseResponse {
  status: string;
  enabled: boolean;
  indices: string[];
  stocks: string[];
  expiries: string[];
  expiry_resolved: string | null;
  errors: string[];
}

export interface FnoCatalogResponse {
  status: string;
  sync_state: unknown;
  segments: unknown;
  fno: {
    available: boolean;
    universe_count: number;
    expiry: string | null;
    errors: string[];
  };
}
