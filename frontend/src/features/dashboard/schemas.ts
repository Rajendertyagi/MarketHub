// Zod contracts for the live market Dashboard. Reuses the shared `quoteSchema`
// projection and extends it with the identity + greeks fields the dashboard and
// quote drawer render. No client-side computation is introduced.
import { z } from "zod";
import { quoteSchema } from "@/api/schemas";

export const greeksSchema = z
  .object({
    delta: z.number().nullable().optional(),
    gamma: z.number().nullable().optional(),
    theta: z.number().nullable().optional(),
    vega: z.number().nullable().optional(),
    rho: z.number().nullable().optional(),
    iv: z.number().nullable().optional(),
  })
  .passthrough();

export const marketQuoteSchema = quoteSchema
  .extend({
    exchange: z.string(),
    instrument_token: z.string(),
    tradingsymbol: z.string().nullable().optional(),
    greeks: greeksSchema.nullable().optional(),
  })
  .passthrough();

export const depthLevelSchema = z.object({
  price: z.number(),
  quantity: z.number(),
  orders: z.number().nullable().optional(),
});

export const quoteDepthSchema = z.object({
  bids: z.array(depthLevelSchema),
  asks: z.array(depthLevelSchema),
});

export const marketQuotesResponseSchema = z.object({
  quotes: z.array(marketQuoteSchema),
});

export type MarketQuote = z.infer<typeof marketQuoteSchema>;
export type QuoteDepth = z.infer<typeof quoteDepthSchema>;
