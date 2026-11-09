// Live market-quote store for the Dashboard. Subscribes to the single market
// SSE stream (via the shared SSEManager) and keeps an in-memory map updated on
// every tick. React state is flushed only when a tick actually changed the map
// (and never while the tab is hidden), so an idle or closed market costs no
// renders. The initial snapshot seeds the map before the stream arrives; the
// fetch is shared across remounts (development StrictMode) instead of running
// twice. A `reset` event clears stale pre-restart state.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { StreamEvent } from "@/streams/SSEManager";
import { useStream } from "@/streams/useStream";
import { getMarketQuotes } from "./api";
import { MARKET_STREAM_KEY, MARKET_STREAM_URL, QUOTE_FLUSH_MS } from "./constants";
import { type MarketQuote, quoteKey } from "./types";

export interface MarketQuotesState {
  quotes: MarketQuote[];
  /** Selected quote per canonical instrument id (S7 coordinator contract). */
  byCanonical: Map<string, MarketQuote>;
  connected: boolean;
}

// One snapshot per page session: concurrent or repeated mounts share the
// in-flight request. A failure clears it so a later mount can retry.
let snapshotPromise: Promise<MarketQuote[]> | null = null;

function loadSnapshot(): Promise<MarketQuote[]> {
  if (!snapshotPromise) {
    snapshotPromise = getMarketQuotes()
      .then((data) => data.quotes)
      .catch(() => {
        snapshotPromise = null;
        return [] as MarketQuote[];
      });
  }
  return snapshotPromise;
}

export function useMarketQuotes(): MarketQuotesState {
  const quotesRef = useRef<Map<string, MarketQuote>>(new Map());
  const [quotes, setQuotes] = useState<MarketQuote[]>([]);
  const [connected, setConnected] = useState(false);
  // Version of the in-memory map; the flush is a no-op when it did not move.
  const versionRef = useRef(0);
  const flushedRef = useRef(0);

  const onQuote = useCallback((e: StreamEvent<MarketQuote>) => {
    if (e.type === "quote" && e.data) {
      quotesRef.current.set(quoteKey(e.data), e.data);
      versionRef.current += 1;
    }
  }, []);

  const onReset = useCallback(() => {
    quotesRef.current.clear();
    versionRef.current += 1;
    flushedRef.current = versionRef.current;
    setQuotes([]);
  }, []);

  useStream<MarketQuote>(MARKET_STREAM_KEY, MARKET_STREAM_URL, onQuote, {
    event: "quote",
    onReset,
    onOpen: () => setConnected(true),
    onError: () => setConnected(false),
  });

  // Publish the live map into React state only when it changed, and never
  // for a hidden tab; becoming visible flushes immediately.
  const flush = useCallback(() => {
    if (document.hidden) return;
    if (versionRef.current === flushedRef.current) return;
    flushedRef.current = versionRef.current;
    setQuotes([...quotesRef.current.values()]);
  }, []);

  // Seed from the snapshot endpoint, then keep the map authoritative via SSE.
  // A live tick that arrived first wins over the older snapshot value.
  useEffect(() => {
    let cancelled = false;
    loadSnapshot().then((seededQuotes) => {
      if (cancelled) return;
      for (const q of seededQuotes) {
        const key = quoteKey(q);
        if (!quotesRef.current.has(key)) quotesRef.current.set(key, q);
      }
      versionRef.current += 1;
      flush();
    });
    return () => {
      cancelled = true;
    };
  }, [flush]);

  useEffect(() => {
    const id = setInterval(flush, QUOTE_FLUSH_MS);
    document.addEventListener("visibilitychange", flush);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", flush);
    };
  }, [flush]);

  // Canonical identity index over the live quotes. The backend emits the
  // canonically-selected quote per instrument, so the first entry per
  // canonical_id IS the selected one (no provider-specific join).
  const byCanonical = useMemo(() => {
    const m = new Map<string, MarketQuote>();
    for (const q of quotes) {
      const cid = (q as { canonical_id?: string | null }).canonical_id;
      if (cid && !m.has(cid)) m.set(cid, q);
    }
    return m;
  }, [quotes]);

  return { quotes, byCanonical, connected };
}
