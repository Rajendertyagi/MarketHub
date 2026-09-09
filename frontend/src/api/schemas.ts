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

export type HistoryResponseParsed = z.infer<typeof historyResponseSchema>;
export type ScannerDefParsed = z.infer<typeof scannerDefSchema>;
export type ScanRowParsed = z.infer<typeof scanRowSchema>;
export type ScanResultParsed = z.infer<typeof scanResultSchema>;
