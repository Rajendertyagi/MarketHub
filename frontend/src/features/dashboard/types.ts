// Domain types for the live market Dashboard. These mirror the backend
// canonical quote projection emitted by the `/api/market/stream` SSE feed and
// the `/api/market/quotes` snapshot. React only renders; it never derives
// prices, computes greeks, or evaluates market state.

export interface Greeks {
  delta?: number | null;
  gamma?: number | null;
  theta?: number | null;
  vega?: number | null;
  rho?: number | null;
  iv?: number | null;
}

export interface MarketQuote {
  exchange: string;
  instrument_token: string;
  tradingsymbol?: string | null;
  ltp?: number | null;
  change?: number | null;
  change_percent?: number | null;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  close?: number | null;
  avg_trade_price?: number | null;
  volume?: number | null;
  open_interest?: number | null;
  oi_change?: number | null;
  oi_change_percent?: number | null;
  best_bid?: number | null;
  best_ask?: number | null;
  bid?: number | null;
  ask?: number | null;
  upper_circuit?: number | null;
  lower_circuit?: number | null;
  last_trade_time?: string | null;
  last_traded_qty?: number | null;
  total_buy_qty?: number | null;
  total_sell_qty?: number | null;
  previous_oi?: number | null;
  greeks?: Greeks | null;
  exchange_ts?: string | null;
  received_ts?: string | null;
  // Backend may attach additional canonical fields; never hard-coded here.
  [key: string]: unknown;
}

export interface DepthLevel {
  price: number;
  quantity: number;
  orders?: number | null;
}

export interface QuoteDepth {
  bids: DepthLevel[];
  asks: DepthLevel[];
}

export interface MarketQuotesResponse {
  quotes: MarketQuote[];
}

// Stable composite key for a quote, matching the backend's exchange:token
// identity. Used for map lookups and table row keys.
export function quoteKey(q: Pick<MarketQuote, "exchange" | "instrument_token">): string {
  return `${q.exchange}:${q.instrument_token}`;
}
