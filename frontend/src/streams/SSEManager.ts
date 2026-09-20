// Single-owner SSE layer for the MarketHub frontend.
//
// Design goals (no backend SSE redesign):
//   - at most ONE EventSource per logical stream key (no duplicate connections)
//   - typed event parsing (backend envelope: `{ type, data }` JSON payload)
//   - subscriber/hook model; native auto-reconnect
//   - cleanup when the last subscriber leaves (no leaked sources)
//   - optional named-event binding (the market stream emits `event: quote`)
//     plus an optional raw `reset` control handler
//
// This module is the shared owner for all SSE streams (market quotes, logs,
// etc.) so the app never opens duplicate EventSource instances.

export interface StreamEvent<T = unknown> {
  type: string;
  data: T;
}

export interface StreamOptions {
  /** SSE event name to bind (e.g. "quote"). Defaults to the unnamed message. */
  event?: string;
  /** Raw "reset" control handler (server asks clients to drop stale state). */
  onReset?: () => void;
  /** Fired when the underlying EventSource opens. */
  onOpen?: () => void;
  /** Fired when the underlying EventSource errors (reconnect pending). */
  onError?: () => void;
}

type StreamHandler<T> = (event: StreamEvent<T>) => void;

interface StreamEntry<T> {
  source: EventSource;
  handlers: Set<StreamHandler<T>>;
}

const streams = new Map<string, StreamEntry<unknown>>();

function parseEnvelope(raw: string): StreamEvent<unknown> | null {
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
  options: StreamOptions = {},
): () => void {
  let entry = streams.get(key) as StreamEntry<T> | undefined;

  if (!entry) {
    const source = new EventSource(url);
    entry = { source, handlers: new Set() };
    // Capture into consts so closures below need no assertions.
    const active = entry;
    const onReset = options.onReset;
    const dispatch = (raw: string) => {
      const env = parseEnvelope(raw);
      if (!env) return;
      for (const h of active.handlers) h(env as StreamEvent<T>);
    };
    if (options.event) {
      source.addEventListener(options.event, (e: MessageEvent) => dispatch(e.data as string));
    } else {
      source.onmessage = (e: MessageEvent) => dispatch(e.data as string);
    }
    if (onReset) {
      source.addEventListener("reset", () => onReset());
    }
    source.onopen = () => options.onOpen?.();
    source.onerror = () => options.onError?.();
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
