// Canonical, backend-aligned constants for the Test Center. Components reference
// these instead of inlining literals (single source of truth).
export const DIAGNOSTIC_MODES = ["quick", "full"] as const;

export const DEFAULT_SYMBOL = "NIFTY";

// Maps a diagnostic status to a badge kind (drives the CSS class suffix).
export const STATUS_KIND: Record<string, string> = {
  PASS: "success",
  FAIL: "danger",
  PARTIAL: "warning",
  UNAVAILABLE: "neutral",
  SKIPPED: "neutral",
};

export function statusKind(status: string): string {
  return STATUS_KIND[status] ?? "neutral";
}
