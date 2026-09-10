// Pure presentation helpers for AI Alerts. Kept separate from data/logic so
// components stay declarative and the formatting is unit-testable.
import { DELIVERY_STATE_KIND, DELIVERY_STATE_LABELS } from "./constants";

// Human-friendly "time ago" from an ISO timestamp (mirrors legacy behavior).
export function timeAgo(ts: string | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts.includes("T") ? ts : ts.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return ts;
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function deliveryBadgeClass(state: string): string {
  const kind = DELIVERY_STATE_KIND[state] ?? "info";
  return `ui-badge ui-badge-${kind}`;
}

export function deliveryLabel(state: string): string {
  return DELIVERY_STATE_LABELS[state] ?? state;
}

export function enabledLabel(enabled: boolean): string {
  return enabled ? "ON" : "OFF";
}
