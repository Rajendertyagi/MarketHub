import { useEffect, useMemo, useRef, useState } from "react";
import { AsyncStateView } from "@/components/ui";
import { ApiError } from "@/types";
import { LOG_MAX_ROWS } from "./constants";
import { useLogStream, useLogs } from "./useLogs";
import type { LogFilters } from "./types";
import { LogsFilterBar } from "./components/LogsFilterBar";
import { LogsTable } from "./components/LogsTable";

const EMPTY_FILTERS: LogFilters = { level: "", logger: "", search: "" };

export function LogsView() {
  const [draft, setDraft] = useState<LogFilters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<LogFilters>(EMPTY_FILTERS);
  const [paused, setPaused] = useState(false);
  const [autoFollow, setAutoFollow] = useState(true);

  const history = useLogs(applied);
  const { records: live, connected } = useLogStream(applied, paused);
  const containerRef = useRef<HTMLDivElement>(null);

  const combined = useMemo(() => {
    const merged = [...live, ...(history.data?.records ?? [])];
    return merged.slice(0, LOG_MAX_ROWS);
  }, [live, history.data]);

  // Auto-follow keeps the newest record (top of the list) in view.
  useEffect(() => {
    if (autoFollow && containerRef.current) {
      containerRef.current.scrollTop = 0;
    }
  }, [combined, autoFollow]);

  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">Logs</h1>
        <span className="muted">Live SSE stream + filtered history</span>
      </div>

      <LogsFilterBar
        draft={draft}
        onChange={setDraft}
        onApply={() => setApplied(draft)}
        onClear={() => {
          setDraft(EMPTY_FILTERS);
          setApplied(EMPTY_FILTERS);
        }}
        paused={paused}
        onTogglePause={() => setPaused((p) => !p)}
        autoFollow={autoFollow}
        onToggleAutoFollow={() => setAutoFollow((a) => !a)}
        connected={connected}
        count={combined.length}
      />

      {history.status === "error" ? (
        <AsyncStateView
          status="error"
          error={history.error as ApiError}
          onRetry={() => history.refetch()}
        />
      ) : (
        <LogsTable ref={containerRef} records={combined} />
      )}
    </div>
  );
}
