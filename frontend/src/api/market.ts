// Typed endpoint modules for the currently migrated features.
//
// Each function is a thin, typed wrapper over the shared client. They preserve
// backend naming/semantics and never recompute canonical values client-side.

import { request } from "./client";
import {
  breadthSchema,
  fnoUniverseSchema,
  fnoWorkspaceSchema,
  fnoViewSchema,
  futuresResponseSchema,
  historyResponseSchema,
  indexOptionUnderlyingsSchema,
  marketMapSchema,
  optionChainViewSchema,
  optionExpiriesSchema,
  scannersListSchema,
  scanResultSchema,
  sectorHeatmapSchema,
} from "./schemas";
import type {
  BreadthSnapshot,
  Candle,
  FnoUniverseResponse,
  FnoViewResponse,
  FnoWorkspace,
  FuturesResponse,
  HistoryResponse,
  IndexOptionUnderlying,
  Instrument,
  MarketMapSnapshot,
  OptionChainView,
  OptionExpiriesResponse,
  ScannerDef,
  ScanResult,
  ScannerRunParams,
  SectorHeatmapSnapshot,
} from "@/types";

export interface HistoryParams {
  instrument_key: string;
  provider?: string;
  unit?: string;
  interval?: number | string;
  from?: string;
  to?: string;
}

export async function getHistory(
  params: HistoryParams,
  signal?: AbortSignal,
): Promise<HistoryResponse> {
  return request<HistoryResponse>("/market/history", {
    params: {
      instrument_key: params.instrument_key,
      provider: params.provider ?? "",
      unit: params.unit ?? "days",
      interval: params.interval ?? 1,
      from: params.from ?? "",
      to: params.to ?? "",
    },
    schema: historyResponseSchema,
    signal,
  });
}

export async function listScanners(
  signal?: AbortSignal,
): Promise<ScannerDef[]> {
  const data = await request<{ status: string; scanners: ScannerDef[] }>(
    "/market/scanners",
    { schema: scannersListSchema, signal },
  );
  return data.scanners;
}

export async function runScanner(
  name: string,
  params: ScannerRunParams,
  signal?: AbortSignal,
): Promise<ScanResult> {
  return request<ScanResult>(`/market/scanner/${encodeURIComponent(name)}`, {
    params: {
      universe: params.universe,
      limit: params.limit,
      expiry: params.expiry ?? "",
      atm_range: params.atm_range ?? "",
      option_type: params.option_type ?? "",
    },
    schema: scanResultSchema,
    signal,
  });
}

export interface InstrumentSearchParams {
  q: string;
  exchange?: string;
  type?: string;
  provider?: string;
  limit?: number;
}

export async function searchInstruments(
  params: InstrumentSearchParams,
  signal?: AbortSignal,
): Promise<Instrument[]> {
  const data = await request<{ results: Instrument[]; count: number }>(
    "/instruments/search",
    {
      params: {
        q: params.q,
        exchange: params.exchange ?? "",
        type: params.type ?? "",
        provider: params.provider ?? "",
        limit: params.limit ?? 25,
      },
      signal,
    },
  );
  return data.results;
}

/**
 * Resolve the EXACT canonical instrument for a tradingsymbol + type.
 *
 * Scanner rows expose only the human tradingsymbol (and, for derivatives, the
 * contract tradingsymbol) — never a fabricated token. To open the chart we ask
 * the catalog to resolve the real instrument identity (instrument_token +
 * exchange + tradingsymbol). This mirrors the legacy `openChart` behavior and
 * guarantees we never substitute one instrument class for another.
 */
export async function resolveInstrument(
  tradingsymbol: string,
  type: string,
  signal?: AbortSignal,
): Promise<Instrument | null> {
  const results = await searchInstruments(
    { q: tradingsymbol, type, limit: 10 },
    signal,
  );
  const match = results.find((r) => r.tradingsymbol === tradingsymbol);
  return match ?? results[0] ?? null;
}

// ── Market analytics (Breadth / Sector Heatmap / Market Map) ─────────────────
// Thin typed wrappers over the canonical aggregated endpoints. They preserve
// backend naming/semantics and never recompute canonical values client-side.

export async function getBreadth(
  universe: string,
  signal?: AbortSignal,
): Promise<BreadthSnapshot> {
  return request<BreadthSnapshot>("/market/breadth", {
    params: { universe },
    schema: breadthSchema,
    signal,
  });
}

export async function getSectorHeatmap(
  universe: string,
  signal?: AbortSignal,
): Promise<SectorHeatmapSnapshot> {
  return request<SectorHeatmapSnapshot>("/market/sector-heatmap", {
    params: { universe, members: 1 },
    schema: sectorHeatmapSchema,
    signal,
  });
}

export async function getMarketMap(
  universe: string,
  signal?: AbortSignal,
): Promise<MarketMapSnapshot> {
  return request<MarketMapSnapshot>("/market/map", {
    params: { universe },
    schema: marketMapSchema,
    signal,
  });
}

// ── F&O Workspace + Option Chain (existing canonical endpoints) ────────────────
// Thin typed wrappers. They preserve backend naming/semantics and never recompute
// canonical values (IV is a fraction, ATM is backend-resolved, greeks/quotes are
// passed through unchanged). The active-view subscription reuses the existing
// bounded owner (POST /api/market/fno/view) — no second subscription owner.

export async function getFnoUniverse(
  q = "",
  limit = 500,
  signal?: AbortSignal,
): Promise<FnoUniverseResponse> {
  return request<FnoUniverseResponse>("/market/fno/universe", {
    params: { q, limit },
    schema: fnoUniverseSchema,
    signal,
  });
}

export interface FnoWorkspaceParams {
  symbol: string;
  window?: number;
  futures?: number;
  expiries?: number;
}

export async function getFnoWorkspace(
  params: FnoWorkspaceParams,
  signal?: AbortSignal,
): Promise<FnoWorkspace> {
  return request<FnoWorkspace>(
    `/market/fno/stock/${encodeURIComponent(params.symbol)}`,
    {
      params: {
        window: params.window ?? 10,
        futures: params.futures ?? 2,
        expiries: params.expiries ?? 1,
      },
      schema: fnoWorkspaceSchema,
      signal,
    },
  );
}

export interface FnoViewParams {
  symbol: string;
  window?: number;
  future_count?: number;
  expiry_count?: number;
}

// Establishes the bounded active-view subscription (equity + futures + ATM±window
// options) and reconciles the live feed. Best-effort from the frontend: the view
// still renders canonical quotes even if coverage cannot be applied.
export async function postFnoView(
  params: FnoViewParams,
  signal?: AbortSignal,
): Promise<FnoViewResponse> {
  return request<FnoViewResponse>("/market/fno/view", {
    method: "POST",
    body: {
      symbol: params.symbol,
      window: params.window ?? 10,
      future_count: params.future_count ?? 2,
      expiry_count: params.expiry_count ?? 1,
    },
    schema: fnoViewSchema,
    signal,
  });
}


export async function getOptionExpiries(
  underlying: string,
  signal?: AbortSignal,
): Promise<OptionExpiriesResponse> {
  return request<OptionExpiriesResponse>("/options/expiries", {
    params: { underlying },
    schema: optionExpiriesSchema,
    signal,
  });
}

// Canonical index option-chain underlyings, owned by the backend
// (app.market_indices). The frontend consumes this instead of hard-coding the
// supported index list.
export async function getIndexOptionUnderlyings(
  signal?: AbortSignal,
): Promise<IndexOptionUnderlying[]> {
  const data = await request<{ underlyings: IndexOptionUnderlying[] }>(
    "/options/index-underlyings",
    { schema: indexOptionUnderlyingsSchema, signal },
  );
  return data.underlyings;
}

export interface OptionChainParams {
  underlying: string;
  expiry?: string;
  window?: number;
}

export async function getOptionChainView(
  params: OptionChainParams,
  signal?: AbortSignal,
): Promise<OptionChainView> {
  return request<OptionChainView>("/options/chain/view", {
    params: {
      underlying: params.underlying,
      expiry: params.expiry ?? "",
      window: params.window ?? 10,
    },
    schema: optionChainViewSchema,
    signal,
  });
}

export async function getFutures(
  underlying: string,
  expiry?: string,
  signal?: AbortSignal,
): Promise<FuturesResponse> {
  return request<FuturesResponse>("/futures", {
    params: { underlying, expiry: expiry ?? "" },
    schema: futuresResponseSchema,
    signal,
  });
}

export type { Candle };
