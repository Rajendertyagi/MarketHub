import type { AlertField, AlertOperator } from "./types";

// Canonical, backend-aligned constants. Components reference these instead of
// inlining literals (single source of truth — mirrors the backend's allowed
// field/operator sets so the two never diverge silently).
//
// NOTE: these are the *fixed* alert contract enums owned by the backend
// (core/persistence/modules/products.py _ALERT_FIELDS / _ALERT_OPERATORS).
// They are not user-fetched config; they are the contract surface.

export const ALERT_FIELDS: readonly AlertField[] = [
  "ltp",
  "change_percent",
  "volume",
  "oi_change_percent",
] as const;

export const ALERT_OPERATORS: readonly AlertOperator[] = [
  "gt",
  "lt",
  "crosses_above",
  "crosses_below",
] as const;

export const ALERT_FIELD_LABELS: Record<AlertField, string> = {
  ltp: "Last Traded Price",
  change_percent: "Change %",
  volume: "Volume",
  oi_change_percent: "OI Change %",
};

export const ALERT_OPERATOR_LABELS: Record<AlertOperator, string> = {
  gt: ">",
  lt: "<",
  crosses_above: "crosses above",
  crosses_below: "crosses below",
};

// Default exchange used when creating an alert (legacy used NSE).
export const DEFAULT_ALERT_EXCHANGE = "NSE";

// Polling cadence — matches the legacy 15s refresh timer.
export const ALERT_REFRESH_MS = 15000;

// Bounded history page size (legacy requested limit=50).
export const ALERT_HISTORY_LIMIT = 50;

// Small static list of well-known NSE symbols used to back the alert-creation
// <datalist> autocomplete. Purely presentational — no broker/API calls.
export const NSE_SYMBOLS: readonly { token: string; label: string }[] = [
  { token: "NSE_EQ|RELIANCE", label: "RELIANCE" },
  { token: "NSE_EQ|INFY", label: "INFY" },
  { token: "NSE_EQ|TCS", label: "TCS" },
  { token: "NSE_EQ|HDFCBANK", label: "HDFCBANK" },
  { token: "NSE_EQ|ICICIBANK", label: "ICICIBANK" },
  { token: "NSE_EQ|SBIN", label: "SBIN" },
  { token: "NSE_EQ|ITC", label: "ITC" },
  { token: "NSE_EQ|WIPRO", label: "WIPRO" },
  { token: "NSE_EQ|HDFC", label: "HDFC" },
  { token: "NSE_EQ|LT", label: "LT" },
  { token: "NSE_EQ|AXISBANK", label: "AXISBANK" },
  { token: "NSE_EQ|KOTAKBANK", label: "KOTAKBANK" },
  { token: "NSE_EQ|MARUTI", label: "MARUTI" },
  { token: "NSE_EQ|SUNPHARMA", label: "SUNPHARMA" },
  { token: "NSE_EQ|TATAMOTORS", label: "TATAMOTORS" },
  { token: "NSE_EQ|TATASTEEL", label: "TATASTEEL" },
  { token: "NSE_EQ|BAJFINANCE", label: "BAJFINANCE" },
  { token: "NSE_EQ|ASIANPAINT", label: "ASIANPAINT" },
];
