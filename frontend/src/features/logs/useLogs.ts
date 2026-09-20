// Data hooks for the Logs feature.
//  - useLogs: react-query snapshot of recent records (GET /api/logs).
//  - useLogStream: browser EventSource over /api/logs/stream, applying the same
//    active filters as the history view so live rows never bypass filters.
// React never produces log records — they are generated server-side.

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { getLogs } from "./api";
import { LOG_MAX_ROWS, LOG_SSE_ENDPOINT } from "./constants";
import type { LogFilters, LogRecord } from "./types";

export function useLogs(filters: LogFilters) {
  return useQuery({
    queryKey: ["logs", "history", filters],
    queryFn: ({ signal }) => getLogs(filters, signal),
  });
}

function passesFilters(rec: LogRecord, f: LogFilters): boolean {
  if (f.level && String(rec.level || "").toUpperCase() !== f.level.toUpperCase()) return false;
  if (
    f.logger &&
    !String(rec.logger || "")
      .toLowerCase()
      .includes(f.logger.toLowerCase())
  )
    return false;
  if (
    f.search &&
    !String(rec.message || "")
      .toLowerCase()
      .includes(f.search.toLowerCase())
  )
    return false;
  return true;
}

export function useLogStream(
  filters: LogFilters,
  paused: boolean,
): { records: LogRecord[]; connected: boolean } {
  const [records, setRecords] = useState<LogRecord[]>([]);
  const [connected, setConnected] = useState(false);
  const filtersRef = useRef(filters);
  const pausedRef = useRef(paused);
  filtersRef.current = filters;
  pausedRef.current = paused;

  useEffect(() => {
    const es = new EventSource(LOG_SSE_ENDPOINT);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (e: MessageEvent) => {
      if (pausedRef.current) return;
      try {
        const rec = JSON.parse(e.data) as LogRecord;
        if (!passesFilters(rec, filtersRef.current)) return;
        setRecords((prev) => [rec, ...prev].slice(0, LOG_MAX_ROWS));
      } catch {
        /* malformed frame — skip */
      }
    };
    return () => es.close();
  }, []);

  return { records, connected };
}
