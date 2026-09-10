import { useEffect, useRef } from "react";
import {
  subscribeStream,
  type StreamEvent,
  type StreamOptions,
} from "./SSEManager";

// React binding for the single-owner SSE layer.
//
// Opens (or joins) the logical stream on mount, delivers typed events to the
// latest handler, and unsubscribes on unmount — guaranteeing no duplicate
// EventSource instances and no leaked connections across rapid navigation.
// Supports named SSE events (e.g. `event: quote`) and a raw `reset` handler.
export function useStream<T>(
  key: string | null,
  url: string,
  handler: (event: StreamEvent<T>) => void,
  options: StreamOptions = {},
): void {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;
  const resetRef = useRef(options.onReset);
  resetRef.current = options.onReset;
  const openRef = useRef(options.onOpen);
  openRef.current = options.onOpen;
  const errorRef = useRef(options.onError);
  errorRef.current = options.onError;
  const event = options.event;

  useEffect(() => {
    if (!key) return;
    const unsubscribe = subscribeStream<T>(key, url, (e) => handlerRef.current(e), {
      event,
      onReset: resetRef.current ? () => resetRef.current?.() : undefined,
      onOpen: openRef.current ? () => openRef.current?.() : undefined,
      onError: errorRef.current ? () => errorRef.current?.() : undefined,
    });
    return unsubscribe;
    // `event` is the only option that changes the wire binding.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, url, event]);
}
