// Domain types for the Price Alerts feature. These mirror the canonical backend
// contracts in api/product_routes.py + core/persistence/modules/products.py.
// React only renders and issues control actions; all evaluation/triggering stays
// server-side (app/alerts.py).

export type AlertField = "ltp" | "change_percent" | "volume" | "oi_change_percent";

export type AlertOperator =
  | "gt"
  | "lt"
  | "crosses_above"
  | "crosses_below";

// A persisted market alert (market_alerts row, returned by GET /api/alerts).
// `enabled` arrives as a SQLite INTEGER (1/0); we normalize it to a boolean.
export interface MarketAlert {
  id: number;
  exchange: string;
  instrument_token: string;
  tradingsymbol: string;
  field: AlertField;
  operator: AlertOperator;
  threshold: number;
  enabled: boolean;
  state: string;
  created_at: string | null;
  triggered_at: string | null;
}

// A live in-memory notification from the alert engine (not durable).
export interface AlertNotification {
  alert_id: number;
  tradingsymbol: string;
  field: AlertField;
  operator: AlertOperator;
  threshold: number;
  value: number | null;
  ts: number;
}

// A durable trigger-history row (alert_trigger_history).
export interface AlertHistoryRow {
  id: number;
  alert_id: number;
  exchange: string | null;
  instrument_token: string | null;
  tradingsymbol: string | null;
  field: AlertField;
  operator: AlertOperator;
  threshold: number | null;
  observed_value: number | null;
  provider: string | null;
  triggered_at: string;
  created_at: string;
}

export interface CreateAlertInput {
  exchange: string;
  instrument_token: string;
  tradingsymbol: string;
  field: AlertField;
  operator: AlertOperator;
  threshold: number;
}

export interface AlertsResponse {
  alerts: MarketAlert[];
  notifications: AlertNotification[];
}

export interface AlertHistoryParams {
  limit?: number;
  offset?: number;
  provider?: string;
  alert_id?: number;
}

export interface AlertHistoryResponse {
  history: AlertHistoryRow[];
  total: number;
  limit: number;
  offset: number;
}
