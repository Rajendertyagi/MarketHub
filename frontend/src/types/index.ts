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
  | "FUTURE"
  | "OPTION"
  | "CURRENCY"
  | "COMMODITY";

export interface Instrument {
  instrument_token: string;
  exchange: string;
  tradingsymbol: string;
  instrument_type: InstrumentType;
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
export const ANALYTICS_UNIVERSES = [
  "FNO",
  "NSE_EQ",
  "NIFTY50",
  "NIFTYNXT50",
  "BANKNIFTY",
] as const;

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

// Typed error taxonomy produced by the API client (see api/client.ts).
export type ApiErrorKind =
  | "network"
  | "http"
  | "parse"
  | "abort"
  | "unknown";

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
