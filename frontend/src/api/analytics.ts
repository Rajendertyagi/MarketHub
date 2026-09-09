// Analytics coverage control (reuses the EXISTING backend owner).
//
// These are side-effecting: they request/clear bounded cash-equity quote
// subscriptions for a universe via the canonical /market/analytics/coverage
// endpoint. They are BEST-EFFORT from the frontend's perspective: the analytics
// endpoints (breadth/heatmap/map) still return whatever canonical quotes exist
// even if coverage cannot be applied (e.g. no live feed). We therefore swallow
// failures so a missing feed never blanks the view. The subscription architecture
// is NOT redesigned here — we only call the existing owner.

import { request } from "./client";

export async function ensureAnalyticsCoverage(universe: string): Promise<void> {
  try {
    await request("/market/analytics/coverage", {
      method: "POST",
      body: { universe },
    });
  } catch {
    // Non-fatal: live coverage unavailable; views still render canonical quotes.
  }
}

export async function clearAnalyticsCoverage(): Promise<void> {
  try {
    await request("/market/analytics/coverage", { method: "DELETE" });
  } catch {
    // Non-fatal.
  }
}
