import { z } from "zod";

// Zod schemas for the Diagnostics contracts. Fail loudly so a backend/frontend
// mismatch surfaces as a typed error.
export const diagnosticResultSchema = z
  .object({
    id: z.string(),
    name: z.string(),
    category: z.string(),
    layer: z.string(),
    status: z.string(),
    message: z.string(),
    duration_ms: z.number(),
    data: z.record(z.unknown()).nullable().optional(),
    classification_reason: z.string().optional(),
  })
  .passthrough();

export const diagnosticRunResultSchema = z
  .object({
    run_at: z.string(),
    duration_ms: z.number(),
    summary: z.record(z.number()),
    failures: z.array(z.string()),
    warnings: z.array(z.string()),
    results: z.array(diagnosticResultSchema),
    symbol: z.string().optional(),
  })
  .passthrough();

export const diagnosticCheckSchema = z.object({
  id: z.string(),
  name: z.string(),
  category: z.string(),
  layer: z.string(),
  mode: z.array(z.string()),
  description: z.string(),
  safe_for_auto_run: z.boolean(),
});

export const diagnosticsChecksSchema = z.object({
  checks: z.array(diagnosticCheckSchema),
});
