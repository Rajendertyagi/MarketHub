/**
 * MarketHub WebUI — analytics cash-equity coverage control.
 *
 * Breadth / Sector Heatmap / Market Map share ONE analytics-universe coverage
 * owner on the backend. Selecting a universe (or entering an analytics view)
 * requests coverage for that universe's cash equities; leaving the analytics
 * views clears it. Failures are non-fatal — the tiles simply stay gray until
 * the next successful reconcile. No broker-socket logic lives here.
 */

import { apiPost, apiDelete } from "./api.js";

const ANALYTICS_VIEWS = new Set(["breadth", "sector-heatmap", "market-map"]);

export async function ensureAnalyticsCoverage(universe) {
  if (!universe) return;
  try {
    await apiPost("/api/market/analytics/coverage", { universe });
  } catch {
    /* non-fatal: data stays gray until the next reconcile */
  }
}

export async function clearAnalyticsCoverage() {
  try {
    await apiDelete("/api/market/analytics/coverage");
  } catch {
    /* non-fatal */
  }
}

export function isAnalyticsView(view) {
  return ANALYTICS_VIEWS.has(view);
}
