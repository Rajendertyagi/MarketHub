// Pure presentation helpers for the Dashboard. No client-side data processing —
// only formatting and a clearly-labelled inferred-status computation (derived
// from IST wall-clock, never broker-confirmed).
import { STALE_QUOTE_MIN } from "./constants";
import type { MarketQuote } from "./types";

export { inferredMarketOpen } from "@/utils/market";

export function isStaleQuote(q: MarketQuote): boolean {
  const lastMs = Date.parse(q.received_ts || "") || 0;
  if (!lastMs) return false;
  return (Date.now() - lastMs) / 60000 > STALE_QUOTE_MIN;
}
