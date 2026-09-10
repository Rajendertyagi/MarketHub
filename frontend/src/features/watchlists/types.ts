// Domain types for Watchlists. Mirror the backend `/api/watchlists` contract.
// React only renders items and resolves live values from the market quote
// stream; it never stores or derives prices.
export interface WatchlistItem {
  id: number;
  watchlist_id?: number | null;
  exchange?: string | null;
  instrument_token: string;
  tradingsymbol?: string | null;
}

export interface Watchlist {
  id: number;
  name: string;
  items: WatchlistItem[];
}

export interface WatchlistsResponse {
  watchlists: Watchlist[];
}
