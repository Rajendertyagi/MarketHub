// Domain types for the Test Center / Diagnostics feature. Mirror the contracts
// in api/diagnostics_routes.py + app/diagnostics.py (DiagnosticsRunner). React
// only renders results; all checks run server-side against real endpoints.

export type DiagnosticMode = "quick" | "full";

export interface DiagnosticResult {
  id: string;
  name: string;
  category: string;
  layer: string;
  status: string;
  message: string;
  duration_ms: number;
  data?: Record<string, unknown> | null;
  classification_reason?: string;
}

export interface DiagnosticRunResult {
  run_at: string;
  duration_ms: number;
  summary: Record<string, number>;
  failures: string[];
  warnings: string[];
  results: DiagnosticResult[];
  symbol?: string;
}

export interface DiagnosticCheck {
  id: string;
  name: string;
  category: string;
  layer: string;
  mode: string[];
  description: string;
  safe_for_auto_run: boolean;
}

export interface DiagnosticsChecksResponse {
  checks: DiagnosticCheck[];
}
