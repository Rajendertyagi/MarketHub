import type {
  NewsSourceType,
  SettingsSection,
} from "./types";

// Canonical, backend-aligned constants. Components reference these instead of
// inlining literals (single source of truth — mirrors the backend's allowed
// field sets so the two never diverge silently).

// Settings navigation sections (mirrors the legacy SECTIONS list that the old
// web/ui used to define). Subscriptions + theme are intentionally
// omitted: they are covered by dedicated React features (/subscriptions,
// ThemeProvider).
export const SETTINGS_SECTIONS: { value: SettingsSection; label: string }[] = [
  { value: "general", label: "General" },
  { value: "brokers", label: "Brokers" },
  { value: "news-sources", label: "News Sources" },
  { value: "ai-mcp", label: "AI / MCP" },
  { value: "data-retention", label: "Data & Retention" },
  { value: "logging", label: "Logging" },
  { value: "backup", label: "Backup" },
  { value: "alerts", label: "Alerts" },
];

// Broker identifiers used in source-control actions.
export const BROKER_UPSTOX = "upstox";
export const BROKER_FYERS = "fyers";

// News source types offered by the UI.
export const NEWS_SOURCE_TYPES: NewsSourceType[] = ["rss", "reddit"];

export const NEWS_SOURCE_TYPE_LABELS: Record<NewsSourceType, string> = {
  rss: "RSS",
  reddit: "Reddit",
};

// Market feed lifecycle action labels. The action verb is also the URL segment.
export const SOURCE_ACTIONS = ["start", "stop", "restart"] as const;
export type SourceAction = (typeof SOURCE_ACTIONS)[number];

// Broker auth state display labels (friendly, server-derived).
export const FEED_STATE_LABELS: Record<string, string> = {
  auth_required: "Stopped — daily login required",
  failed: "Failed",
  stopped: "Stopped",
  streaming: "Streaming",
  connecting: "Connecting",
  authorizing: "Authorizing",
  reconnecting: "Reconnecting",
};

export const FEED_STATE_SHORT_LABELS: Record<string, string> = {
  auth_required: "Login Required",
  streaming: "Streaming",
  connecting: "Connecting",
  authorizing: "Authorizing",
  reconnecting: "Reconnecting",
  failed: "Failed",
  stopped: "Stopped",
};

// Stop-reason labels (backend-enumerated, never provider material).
export const STOP_REASON_LABELS: Record<string, string> = {
  operator_stop: "Operator stopped (Stop Feed)",
  application_shutdown: "Application shutdown",
  restart: "Restarted",
  auth_required: "Daily login required",
  cancelled: "Cancelled",
  stop_requested: "Stop requested",
};

// Polling cadence for the live market-source detail tables (matches legacy 10s).
export const SOURCES_REFRESH_MS = 10000;

// Default public base URL requirement note length cap (informational only).
export const GENERAL_REFRESH_MS = 0;
