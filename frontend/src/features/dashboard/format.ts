// Pure presentation helpers for the Dashboard. No client-side data processing —
// only formatting and a clearly-labelled inferred-status computation (derived
// from IST wall-clock, never broker-confirmed).
import {
  MARKET_CLOSE_IST_MIN,
  MARKET_OPEN_IST_MIN,
  STALE_QUOTE_MIN,
} from "./constants";
import type { MarketQuote } from "./types";

// Infer NSE equity session open/closed from IST time. Labelled "inferred" in UI.
export function inferredMarketOpen(now: Date = new Date()): boolean {
  const ist = new Date(now.getTime() + (330 + now.getTimezoneOffset()) * 60000);
  const day = ist.getDay();
  const mins = ist.getHours() * 60 + ist.getMinutes();
  return day >= 1 && day <= 5 && mins >= MARKET_OPEN_IST_MIN && mins <= MARKET_CLOSE_IST_MIN;
}

export function isStaleQuote(q: MarketQuote): boolean {
  const lastMs = Date.parse(q.received_ts || "") || 0;
  if (!lastMs) return false;
  return (Date.now() - lastMs) / 60000 > STALE_QUOTE_MIN;
}
