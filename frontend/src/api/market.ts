// Typed endpoint modules for the currently migrated features.
//
// Each function is a thin, typed wrapper over the shared client. They preserve
// backend naming/semantics and never recompute canonical values client-side.

import type {
  ApplyResult,
  BarsResponse,
  BreadthSnapshot,
  BrowseResponse,
  Candle,
  DerivativeRule,
  FnoApplyResult,
  FnoCatalogResponse,
  FnoCoverageSnapshot,
  FnoCoverageUniverseResponse,
  FnoUniverseResponse,
  FnoViewResponse,
  FnoWorkspace,
  FuturesHeatSnapshot,
  FuturesResponse,
  HistoryResponse,
  IndexOptionUnderlying,
  Instrument,
  MarketInstrument,
  MarketMapSnapshot,
  OptionChainView,
  OptionExpiriesResponse,
  PoolResponse,
  ScannerDef,
  ScannerRunParams,
  ScanResult,
  SectorHeatmapSnapshot,
  SegmentsResponse,
  SubscriptionPreferences,
  SyncProviderState,
  SyncResult,
} from "@/types";
import { request } from "./client";
import {
  applyResultSchema,
  barsResponseSchema,
  breadthSchema,
  browseResponseSchema,
  fnoApplySchema,
  fnoCoverageSchema,
  fnoCoverageUniverseSchema,
  fnoUniverseSchema,
  fnoViewSchema,
  fnoWorkspaceSchema,
  futuresHeatmapSchema,
  futuresResponseSchema,
  historyResponseSchema,
  indexOptionUnderlyingsSchema,
  marketMapSchema,
  marketSearchResponseSchema,
  optionChainViewSchema,
  optionExpiriesSchema,
  poolResponseSchema,
  scannersListSchema,
  scanResultSchema,
  sectorHeatmapSchema,
  segmentsResponseSchema,
  subscriptionPreferencesSchema,
  syncStateSchema,
} from "./schemas";

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

export interface MarketBarsParams {
  instrument_key: string;
  /** Timeframe in minutes (bare integer string contract used by the chart). */
  timeframe: number | string;
  provider?: string;
  /** Bar-open bounds, epoch milliseconds. */
  from?: number;
  to?: number;
  limit?: number;
}

export async function getMarketBars(
  params: MarketBarsParams,
  signal?: AbortSignal,
): Promise<BarsResponse> {
  return request<BarsResponse>("/market/bars", {
    params: {
      instrument_key: params.instrument_key,
      timeframe: String(params.timeframe),
      provider: params.provider ?? "",
      from: params.from,
      to: params.to,
      limit: params.limit,
    },
    schema: barsResponseSchema,
    signal,
  });
}

export async function listScanners(signal?: AbortSignal): Promise<ScannerDef[]> {
  const data = await request<{ status: string; scanners: ScannerDef[] }>("/market/scanners", {
    schema: scannersListSchema,
    signal,
  });
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

export interface MarketSearchParams {
  q: string;
  /** Canonical classes to keep (EQUITY, INDEX, FUTURE, OPTION, ETF). */
  types?: string[];
  exchange?: string;
  expiry?: string;
  limit?: number;
  /** Exact resolver mode: symbol/underlying equality, never substring. */
  exact?: boolean;
}

/**
 * Ranked canonical search (GET /api/market/search).
 *
 * The backend dedupes providers and returns cash classes first, so one row is
 * one real instrument with the provider that owns `provider_token`.
 */
export async function searchMarketInstruments(
  params: MarketSearchParams,
  signal?: AbortSignal,
): Promise<MarketInstrument[]> {
  const data = await request<{ count: number; results: MarketInstrument[] }>("/market/search", {
    params: {
      q: params.q,
      types: params.types?.length ? params.types.join(",") : undefined,
      exchange: params.exchange,
      expiry: params.expiry,
      limit: params.limit ?? 25,
      exact: params.exact ? 1 : undefined,
    },
    schema: marketSearchResponseSchema,
    signal,
  });
  return data.results;
}

export interface BrowseParams {
  /** Canonical classes; backend defaults to INDEX/EQUITY/ETF when omitted. */
  types?: string[];
  exchange?: string;
  segment?: string;
  /** Required when types includes OPTION (backend rejects unscoped options). */
  underlying?: string;
  expiry?: string;
  option_type?: "CE" | "PE";
  strike?: number;
  include_expired?: boolean;
  /** Opaque keyset cursor from a previous page; bound to the filter set. */
  cursor?: string;
  limit?: number;
}

/**
 * Paginated canonical browse (GET /api/instruments/browse, Phase 1).
 *
 * Provider-deduped keyset pagination: pass `next_cursor` back unchanged with
 * the SAME filters to continue. Rows carry the same compact canonical identity
 * as search — `provider_token` is the bare token, never `instrument_key`.
 */
export async function browseInstruments(
  params: BrowseParams,
  signal?: AbortSignal,
): Promise<BrowseResponse> {
  return request<BrowseResponse>("/instruments/browse", {
    params: {
      types: params.types?.length ? params.types.join(",") : undefined,
      exchange: params.exchange,
      segment: params.segment,
      underlying: params.underlying,
      expiry: params.expiry,
      option_type: params.option_type,
      strike: params.strike,
      include_expired: params.include_expired ? 1 : undefined,
      cursor: params.cursor,
      limit: params.limit ?? 50,
    },
    schema: browseResponseSchema,
    signal,
  });
}

/** Curated pool for Vela's native symbol picker (bounded, deterministic). */
export async function getInstrumentPool(
  params: { limit?: number } = {},
  signal?: AbortSignal,
): Promise<PoolResponse> {
  return request<PoolResponse>("/instruments/pool", {
    params: { limit: params.limit },
    schema: poolResponseSchema,
    signal,
  });
}

/**
 * Resolve EXACTLY one canonical instrument (`exact=1`).
 *
 * No substring guessing and no `results[0]` substitution: an unknown symbol
 * returns null instead of a different instrument class.
 */
export async function resolveMarketInstrument(
  symbol: string,
  opts: { exchange?: string; type?: string } = {},
  signal?: AbortSignal,
): Promise<MarketInstrument | null> {
  const rows = await searchMarketInstruments(
    { q: symbol, exchange: opts.exchange, exact: true, limit: 20 },
    signal,
  );
  const want = opts.type?.toUpperCase();
  return rows.find((r) => !want || r.type.toUpperCase() === want) ?? null;
}

/** Map the canonical search row onto the chart's Instrument shape. */
export function toInstrument(row: MarketInstrument): Instrument {
  return {
    instrument_token: row.provider_token,
    exchange: row.exchange,
    tradingsymbol: row.symbol,
    instrument_type: row.type,
    provider: row.provider,
  };
}

/**
 * Resolve the EXACT canonical instrument for a tradingsymbol + type.
 *
 * Scanner rows expose only the human tradingsymbol (and, for derivatives, the
 * contract tradingsymbol) — never a fabricated token. Resolution goes through
 * the canonical exact resolver, so a scanner row can never open a different
 * instrument class.
 */
export async function resolveInstrument(
  tradingsymbol: string,
  type: string,
  signal?: AbortSignal,
): Promise<Instrument | null> {
  const match = await resolveMarketInstrument(tradingsymbol, { type }, signal);
  return match ? toInstrument(match) : null;
}

// ── Market analytics (Breadth / Sector Heatmap / Market Map) ─────────────────
// Thin typed wrappers over the canonical aggregated endpoints. They preserve
// backend naming/semantics and never recompute canonical values client-side.

export async function getBreadth(universe: string, signal?: AbortSignal): Promise<BreadthSnapshot> {
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
  return request<FnoWorkspace>(`/market/fno/stock/${encodeURIComponent(params.symbol)}`, {
    params: {
      window: params.window ?? 10,
      futures: params.futures ?? 2,
      expiries: params.expiries ?? 1,
    },
    schema: fnoWorkspaceSchema,
    signal,
  });
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

// ── Subscriptions (DB-backed preference projection) ───────────────────────────
// React is only the control surface. The DB remains the source of truth; the
// backend resolves actual contracts and reconciles the live feed. No client-side
// expiry rollover or strike discovery.
export async function getSubscriptions(signal?: AbortSignal): Promise<SubscriptionPreferences> {
  return request<SubscriptionPreferences>("/subscriptions", {
    schema: subscriptionPreferencesSchema,
    signal,
  });
}

export async function setIndexEnabled(
  label: string,
  enabled: boolean,
  signal?: AbortSignal,
): Promise<unknown> {
  return request("/subscriptions/indices", {
    method: "PATCH",
    body: { label, enabled },
    signal,
  });
}

export async function addStock(key: string, label: string, signal?: AbortSignal): Promise<unknown> {
  return request("/subscriptions/stocks", {
    method: "POST",
    body: { key, label },
    signal,
  });
}

export async function setStockEnabled(
  key: string,
  enabled: boolean,
  signal?: AbortSignal,
): Promise<unknown> {
  return request("/subscriptions/stocks", {
    method: "PATCH",
    body: { key, enabled },
    signal,
  });
}

export async function removeStock(key: string, signal?: AbortSignal): Promise<unknown> {
  return request("/subscriptions/stocks", {
    method: "DELETE",
    params: { key },
    signal,
  });
}

export async function putDerivativeRule(
  rule: DerivativeRule,
  signal?: AbortSignal,
): Promise<unknown> {
  return request("/subscriptions/rules", {
    method: "PUT",
    body: rule,
    signal,
  });
}

export async function deleteDerivativeRule(
  underlying: string,
  signal?: AbortSignal,
): Promise<unknown> {
  return request("/subscriptions/rules", {
    method: "DELETE",
    params: { underlying },
    signal,
  });
}

// Reconcile the live feed to the current DB-backed preferences (no restart).
export async function applySubscriptions(signal?: AbortSignal): Promise<ApplyResult> {
  return request<ApplyResult>("/subscriptions/apply", {
    method: "POST",
    schema: applyResultSchema,
    signal,
  });
}

// ── Instruments / catalog (segment preferences + master sync) ────────────────
// React reads catalog/source status and edits segment preferences; it never
// parses provider master files or infers instrument type client-side.
export async function getSegments(signal?: AbortSignal): Promise<SegmentsResponse> {
  return request<SegmentsResponse>("/instruments/segments", {
    schema: segmentsResponseSchema,
    signal,
  });
}

export async function setSegments(segments: string[], signal?: AbortSignal): Promise<unknown> {
  return request("/instruments/segments", {
    method: "PUT",
    body: { segments },
    signal,
  });
}

export async function syncInstruments(provider: string, signal?: AbortSignal): Promise<SyncResult> {
  return request<SyncResult>("/instruments/sync", {
    method: "POST",
    body: { provider },
    signal,
  });
}

export async function getSyncState(
  signal?: AbortSignal,
): Promise<{ providers: SyncProviderState[] }> {
  return request<{ providers: SyncProviderState[] }>("/instruments/sync-state", {
    schema: syncStateSchema,
    signal,
  });
}

export type { Candle };

// ── F&O coverage / Futures Board (/api/futures/heatmap, /api/fno/*) ──────────

export async function getFuturesHeatmap(
  expiry = "latest",
  buildup = "all",
  signal?: AbortSignal,
): Promise<FuturesHeatSnapshot> {
  return request<FuturesHeatSnapshot>("/futures/heatmap", {
    params: { expiry, buildup },
    schema: futuresHeatmapSchema,
    signal,
  });
}

export async function getFnoCoverage(
  expiry?: string,
  signal?: AbortSignal,
): Promise<FnoCoverageSnapshot> {
  return request<FnoCoverageSnapshot>("/fno/coverage", {
    params: { expiry: expiry ?? "" },
    schema: fnoCoverageSchema,
    signal,
  });
}

export async function previewFnoCoverage(
  expiry?: string,
  signal?: AbortSignal,
): Promise<FnoCoverageSnapshot> {
  return request<FnoCoverageSnapshot>("/fno/preview", {
    params: { expiry: expiry ?? "" },
    signal,
  });
}

export async function applyFnoCoverage(
  expiry?: string,
  signal?: AbortSignal,
): Promise<FnoApplyResult> {
  return request<FnoApplyResult>("/fno/apply", {
    method: "POST",
    body: { expiry: expiry ?? null },
    schema: fnoApplySchema,
    signal,
  });
}

export async function releaseFnoCoverage(signal?: AbortSignal): Promise<FnoApplyResult> {
  return request<FnoApplyResult>("/fno/apply", {
    method: "DELETE",
    schema: fnoApplySchema,
    signal,
  });
}

export async function getFnoCoverageUniverse(
  expiry?: string,
  signal?: AbortSignal,
): Promise<FnoCoverageUniverseResponse> {
  return request<FnoCoverageUniverseResponse>("/fno/universe", {
    params: { expiry: expiry ?? "" },
    schema: fnoCoverageUniverseSchema,
    signal,
  });
}

export async function getFnoCatalog(signal?: AbortSignal): Promise<FnoCatalogResponse> {
  return request<FnoCatalogResponse>("/fno/catalog", { signal });
}
