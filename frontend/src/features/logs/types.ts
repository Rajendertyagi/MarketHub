// Domain types for the Logs feature. Mirror the structured log-record contract
// in core/log_buffer.py (LogRecord.to_dict) consumed by GET /api/logs and the
// /api/logs/stream SSE feed. React only renders; records are produced server-side.

export type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";

export interface LogRecord {
  ts: string;
  level: string;
  logger: string;
  message: string;
  event?: string | null;
  request_id?: string | null;
  consumer_id?: string | null;
  alert_id?: string | null;
  event_id?: string | null;
  broker?: string | null;
  exception?: string | null;
  extra?: Record<string, unknown> | null;
}

export interface LogFilters {
  level?: string;
  logger?: string;
  search?: string;
  consumer_id?: string;
  alert_id?: string;
  request_id?: string;
}

export interface LogsResponse {
  status: string;
  count: number;
  records: LogRecord[];
}
