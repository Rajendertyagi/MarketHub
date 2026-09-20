// Thin API layer for the Test Center. Every call goes through the shared
// `request()` helper (owns the /api prefix + Zod validation) — no hardcoded
// URLs, no direct fetch. Diagnostics are server-side; React only renders.
import { request } from "@/api/client";
import { diagnosticRunResultSchema, diagnosticsChecksSchema } from "./schemas";
import type { DiagnosticMode, DiagnosticRunResult, DiagnosticsChecksResponse } from "./types";

export async function getDiagnosticsChecks(
  signal?: AbortSignal,
): Promise<DiagnosticsChecksResponse> {
  return request<DiagnosticsChecksResponse>("/diagnostics/checks", {
    schema: diagnosticsChecksSchema,
    signal,
  });
}

export async function runDiagnostics(
  mode: DiagnosticMode,
  symbol: string,
  signal?: AbortSignal,
): Promise<DiagnosticRunResult> {
  return request<DiagnosticRunResult>("/diagnostics/run", {
    params: { mode, symbol: symbol || "NIFTY" },
    schema: diagnosticRunResultSchema,
    signal,
  });
}
