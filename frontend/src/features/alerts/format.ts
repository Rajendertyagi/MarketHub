// Pure presentation helpers for alert conditions. Kept separate from data/logic
// so components stay declarative and the formatting logic is unit-testable.
import { ALERT_FIELD_LABELS, ALERT_OPERATOR_LABELS } from "./constants";
import type { AlertField, AlertOperator } from "./types";

export function formatOperator(op: AlertOperator): string {
  return ALERT_OPERATOR_LABELS[op] ?? op;
}

// Human-readable condition, e.g. "LTP > 2500" or "Change % crosses above 5".
export function formatCondition(
  field: AlertField,
  operator: AlertOperator,
  threshold: number,
): string {
  const label = ALERT_FIELD_LABELS[field] ?? field;
  return `${label} ${formatOperator(operator)} ${threshold}`;
}

export function formatTimestamp(iso: string | null): string {
  if (!iso) return "—";
  // Normalize the ISO string for cross-browser Date parsing.
  const d = new Date(iso.includes("T") ? iso : iso.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}
