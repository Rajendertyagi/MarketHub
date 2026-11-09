// Pure presentation helpers for the Dashboard. No client-side data processing —
// only formatting and a clearly-labelled inferred-status computation (derived
// from IST wall-clock, never broker-confirmed).
import { STALE_QUOTE_MIN } from "./constants";
import type { MarketQuote } from "./types";

export { inferredMarketOpen } from "@/utils/market";

export function isStaleQuote(q: MarketQuote): boolean {
  // The backend owns the freshness verdict: it knows the instrument kind
  // and the feed's tick cadence, and it reports ``stale`` together with
  // ``as_of``. The local threshold is only a fallback for a payload that
  // carries no verdict.
  const verdict = q.stale;
  if (typeof verdict === "boolean") return verdict;
  const lastMs = Date.parse(q.received_ts || "") || 0;
  if (!lastMs) return false;
  return (Date.now() - lastMs) / 60000 > STALE_QUOTE_MIN;
}
