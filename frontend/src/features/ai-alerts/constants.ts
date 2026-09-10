// Canonical, backend-aligned display constants for AI Alerts. Components
// reference these instead of inlining literals (single source of truth).

export const DELIVERY_STATE_LABELS: Record<string, string> = {
  acknowledged: "Acknowledged",
  pending: "Pending",
  persisted: "Persisted",
};

// Maps a delivery state to a badge kind (drives the CSS class suffix).
export const DELIVERY_STATE_KIND: Record<string, string> = {
  acknowledged: "success",
  pending: "warning",
  persisted: "info",
};

export const AI_ALERT_REFRESH_MS = 15000;
export const AI_EVENTS_LIMIT = 200;

// Shorten a long id (uuid/sequence) for compact table display.
export function shortId(id: string | number | null | undefined): string {
  if (id == null) return "—";
  const s = String(id);
  return s.length > 12 ? s.slice(0, 12) : s;
}
