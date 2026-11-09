// Canonical, backend-aligned constants for the X/Twitter feature.

export const X_POLL_FLOOR_SECONDS = 60;
export const X_DEFAULT_LIMIT_MAX = 20;

// Poll/status refresh cadence (matches source polling).
export const X_STATUS_REFRESH_MS = 10000;

export const X_AUTH_STATE_LABELS: Record<string, string> = {
  not_configured: "Not configured",
  configured: "Configured (unverified)",
  authenticated: "Authenticated",
  "expired/invalid": "Expired / invalid",
  rate_limited: "Rate limited",
};
