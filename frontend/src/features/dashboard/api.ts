// Thin API layer for the Dashboard. Every call goes through the shared
// `request()` helper (owns the /api prefix + Zod validation) — no hardcoded
// URLs, no direct fetch, no client-side quote processing.
import { request } from "@/api/client";
import {
  marketQuotesResponseSchema,
  quoteDepthSchema,
} from "./schemas";
import { MARKET_QUOTES_URL } from "./constants";

export async function getMarketQuotes(
  signal?: AbortSignal,
): Promise<{ quotes: import("./types").MarketQuote[] }> {
  return request(MARKET_QUOTES_URL, {
    schema: marketQuotesResponseSchema,
    signal,
  });
}

export async function getQuoteDepth(
  exchange: string,
  instrumentToken: string,
  signal?: AbortSignal,
): Promise<import("./types").QuoteDepth> {
  return request(`/api/market/depth/${encodeURIComponent(exchange)}/${encodeURIComponent(instrumentToken)}`, {
    schema: quoteDepthSchema,
    signal,
  });
}
