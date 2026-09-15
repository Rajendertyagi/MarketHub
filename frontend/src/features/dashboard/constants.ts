// Non-magic constants for the Dashboard feature. Kept in one place so views and
// hooks share identical timing/labels without scattered literals.
export const MARKET_STREAM_KEY = "market";
export const MARKET_STREAM_URL = "/api/market/stream";

// Snapshot endpoint for the initial subscribed-universe load (before SSE fills).
export const MARKET_QUOTES_URL = "/api/market/quotes";

// How often the throttled view re-reads the live quote map (ms). The SSE layer
// updates an in-memory map on every tick; React re-renders at this cadence to
// stay smooth without per-tick state churn.
export const QUOTE_FLUSH_MS = 1000;

// Stale-data threshold: quotes older than this (minutes) are flagged so old
// prices never read as live ticks.
export const STALE_QUOTE_MIN = 5;

export const MOVER_CATEGORIES = ["Top Gainer", "Top Loser", "Volume Leader"] as const;
export type MoverCategory = (typeof MOVER_CATEGORIES)[number];
