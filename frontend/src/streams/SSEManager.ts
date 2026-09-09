// Single-owner SSE layer for the MarketHub frontend.
//
// Design goals (no backend SSE redesign):
//   - at most ONE EventSource per logical stream key (no duplicate connections)
//   - typed event parsing (backend envelope: `event: quote` + JSON payload)
//   - subscriber/hook model; native auto-reconnect
//   - cleanup when the last subscriber leaves (no leaked sources)
//
// This module is intentionally NOT wired into REST-only features yet; it exists
// so future migrations (quotes, breadth, market map) share one coherent owner.

export interface StreamEvent<T = unknown> {
  type: string;
  data: T;
}

type StreamHandler<T> = (event: StreamEvent<T>) => void;

interface StreamEntry<T> {
  source: EventSource;
  handlers: Set<StreamHandler<T>>;
}

const streams = new Map<string, StreamEntry<unknown>>();

function parseEnvelope(raw: string): StreamEvent<unknown> | null {
  // The market stream also emits a non-JSON "reset" control token; ignore it
  // here (consumers that need reset can special-case the raw stream).
  try {
    const data = JSON.parse(raw);
    if (data && typeof data === "object" && "type" in data) {
      return data as StreamEvent<unknown>;
    }
  } catch {
    return null;
  }
  return null;
}

export function subscribeStream<T>(
  key: string,
  url: string,
  handler: StreamHandler<T>,
): () => void {
  let entry = streams.get(key) as StreamEntry<T> | undefined;

  if (!entry) {
    const source = new EventSource(url);
    entry = { source, handlers: new Set() };
    source.onmessage = (e: MessageEvent) => {
      const env = parseEnvelope(e.data as string);
      if (!env) return;
      for (const h of entry!.handlers) h(env as StreamEvent<T>);
    };
    streams.set(key, entry as StreamEntry<unknown>);
  }

  entry.handlers.add(handler as StreamHandler<unknown>);

  return () => {
    const current = streams.get(key) as StreamEntry<T> | undefined;
    if (!current) return;
    current.handlers.delete(handler as StreamHandler<unknown>);
    if (current.handlers.size === 0) {
      current.source.close();
      streams.delete(key);
    }
  };
}

export function closeStream(key: string): void {
  const entry = streams.get(key);
  if (entry) {
    entry.source.close();
    streams.delete(key);
  }
}
