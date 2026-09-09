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
