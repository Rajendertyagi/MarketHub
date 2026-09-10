// Zod contracts for Watchlists. Permissive on item shape (backend owns it).
import { z } from "zod";

export const watchlistItemSchema = z
  .object({
    id: z.number(),
    watchlist_id: z.number().nullable().optional(),
    exchange: z.string().nullable().optional(),
    instrument_token: z.union([z.string(), z.number()]).transform(String),
    tradingsymbol: z.string().nullable().optional(),
  })
  .passthrough();

export const watchlistSchema = z
  .object({
    id: z.number(),
    name: z.string(),
    items: z.array(watchlistItemSchema),
  })
  .passthrough();

export const watchlistsResponseSchema = z.object({
  watchlists: z.array(watchlistSchema),
});

export type WatchlistItem = z.infer<typeof watchlistItemSchema>;
export type Watchlist = z.infer<typeof watchlistSchema>;
