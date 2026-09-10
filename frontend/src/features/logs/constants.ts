// Canonical, backend-aligned constants for the Logs view. Components reference
// these instead of inlining literals (single source of truth).
export const LOG_LEVELS: readonly string[] = [
  "DEBUG",
  "INFO",
  "WARNING",
  "ERROR",
  "CRITICAL",
] as const;

// Snapshot page size requested from GET /api/logs (legacy used 300).
export const LOG_HISTORY_LIMIT = 300;

// Hard cap on rows kept in the browser DOM (legacy used 500).
export const LOG_MAX_ROWS = 500;

// SSE endpoint for the live log stream (relative to server root).
export const LOG_SSE_ENDPOINT = "/api/logs/stream";
