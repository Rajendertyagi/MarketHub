// Candle normalization for chronological rendering.
//
// The backend is the source of truth for history. The recent runtime report
// showed history results returned newest-first. We MUST NOT mutate backend
// semantics, but the chart must render chronologically (oldest -> newest).
//
// `normalizeCandles` therefore sorts defensively by timestamp ascending. It
// returns a NEW array (no in-place mutation of the canonical payload) and is
// idempotent for already-ascending input.

import type { Candle } from "@/types";

function tsOf(c: Candle): number {
  const t = Date.parse(c.timestamp);
  return Number.isNaN(t) ? 0 : t;
}

export function normalizeCandles(candles: Candle[]): Candle[] {
  if (!Array.isArray(candles)) return [];
  return [...candles].sort((a, b) => tsOf(a) - tsOf(b));
}

export function isChronological(candles: Candle[]): boolean {
  for (let i = 1; i < candles.length; i++) {
    const cur = candles[i];
    const prev = candles[i - 1];
    // Guarded for noUncheckedIndexedAccess; unreachable by loop bounds.
    if (cur === undefined || prev === undefined) continue;
    if (tsOf(cur) < tsOf(prev)) return false;
  }
  return true;
}
