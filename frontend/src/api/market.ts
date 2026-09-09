// Typed endpoint modules for the currently migrated features.
//
// Each function is a thin, typed wrapper over the shared client. They preserve
// backend naming/semantics and never recompute canonical values client-side.

import { request } from "./client";
import {
  breadthSchema,
  historyResponseSchema,
  marketMapSchema,
  scannersListSchema,
  scanResultSchema,
  sectorHeatmapSchema,
} from "./schemas";
import type {
  BreadthSnapshot,
  Candle,
  HistoryResponse,
  Instrument,
  MarketMapSnapshot,
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

export type { Candle };
