// Live market-quote store for the Dashboard. Subscribes to the single market
// SSE stream (via the shared SSEManager) and keeps an in-memory map updated on
// every tick. React state is flushed at a fixed cadence (QUOTE_FLUSH_MS) so the
// UI stays smooth without per-tick re-renders. The initial snapshot seeds the
// map before the stream arrives. A `reset` event clears stale pre-restart state.
import { useCallback, useEffect, useRef, useState } from "react";
import type { StreamEvent } from "@/streams/SSEManager";
import { useStream } from "@/streams/useStream";
import { getMarketQuotes } from "./api";
import { MARKET_STREAM_KEY, MARKET_STREAM_URL, QUOTE_FLUSH_MS } from "./constants";
import { type MarketQuote, quoteKey } from "./types";

export interface MarketQuotesState {
  quotes: MarketQuote[];
  connected: boolean;
}

export function useMarketQuotes(): MarketQuotesState {
  const quotesRef = useRef<Map<string, MarketQuote>>(new Map());
  const [quotes, setQuotes] = useState<MarketQuote[]>([]);
  const [connected, setConnected] = useState(false);

  const onQuote = useCallback((e: StreamEvent<MarketQuote>) => {
    if (e.type === "quote" && e.data) {
      quotesRef.current.set(quoteKey(e.data), e.data);
    }
  }, []);

  const onReset = useCallback(() => {
    quotesRef.current.clear();
    setQuotes([]);
  }, []);

  useStream<MarketQuote>(MARKET_STREAM_KEY, MARKET_STREAM_URL, onQuote, {
    event: "quote",
    onReset,
    onOpen: () => setConnected(true),
    onError: () => setConnected(false),
  });

  // Seed from the snapshot endpoint, then keep the map authoritative via SSE.
  useEffect(() => {
    let cancelled = false;
    getMarketQuotes()
      .then((data) => {
        if (cancelled) return;
        for (const q of data.quotes) quotesRef.current.set(quoteKey(q), q);
        setQuotes([...quotesRef.current.values()]);
      })
      .catch(() => {
        /* SSE will populate */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Throttled flush of the live map into React state.
  useEffect(() => {
    const id = setInterval(() => {
      setQuotes([...quotesRef.current.values()]);
    }, QUOTE_FLUSH_MS);
    return () => clearInterval(id);
  }, []);

  return { quotes, connected };
}
