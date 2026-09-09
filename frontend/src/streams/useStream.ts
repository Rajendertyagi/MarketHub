import { useEffect, useRef } from "react";
import { subscribeStream, type StreamEvent } from "./SSEManager";

// React binding for the single-owner SSE layer.
//
// Opens (or joins) the logical stream on mount, delivers typed events to the
// latest handler, and unsubscribes on unmount — guaranteeing no duplicate
// EventSource instances and no leaked connections across rapid navigation.
export function useStream<T>(
  key: string | null,
  url: string,
  handler: (event: StreamEvent<T>) => void,
): void {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    if (!key) return;
    const unsubscribe = subscribeStream<T>(key, url, (e) => handlerRef.current(e));
    return unsubscribe;
  }, [key, url]);
}
