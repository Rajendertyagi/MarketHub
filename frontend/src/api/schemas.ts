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

export const scannerDefSchema = z.object({
  name: z.string(),
  title: z.string(),
  instrument_class: z.string(),
  metric: z.string(),
  order: z.string(),
  contract_kind: z
    .enum(["future", "option"])
    .nullable()
    .optional(),
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
  top_gainer: z.record(z.unknown()).nullable(),
  top_loser: z.record(z.unknown()).nullable(),
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
  reconciliation: z.record(z.unknown()),
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
  reconciliation: z.record(z.unknown()),
});

export type HistoryResponseParsed = z.infer<typeof historyResponseSchema>;
export type ScannerDefParsed = z.infer<typeof scannerDefSchema>;
export type ScanRowParsed = z.infer<typeof scanRowSchema>;
export type ScanResultParsed = z.infer<typeof scanResultSchema>;
export type BreadthSnapshotParsed = z.infer<typeof breadthSchema>;
export type SectorHeatmapSnapshotParsed = z.infer<typeof sectorHeatmapSchema>;
export type MarketMapSnapshotParsed = z.infer<typeof marketMapSchema>;
