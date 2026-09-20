import { useEffect, useState } from "react";
import { AsyncStateView, Button, Field, Select } from "@/components/ui";
import type { ApiError } from "@/types";
import { useInstrumentMutations, useSegments, useSyncState } from "./useInstruments";

type SyncPhase = "idle" | "running" | "success" | "error";

export function InstrumentsView() {
  const segmentsQuery = useSegments();
  const syncStateQuery = useSyncState();
  const { saveSegments, sync } = useInstrumentMutations();

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [provider, setProvider] = useState("upstox");
  const [syncPhase, setSyncPhase] = useState<SyncPhase>("idle");
  const [syncMsg, setSyncMsg] = useState<string>("");

  // Initialize the editable selection from the backend's saved enabled set. We
  // do NOT overwrite saved preferences just because they differ from defaults.
  useEffect(() => {
    if (segmentsQuery.data) {
      setSelected(new Set(segmentsQuery.data.enabled));
    }
  }, [segmentsQuery.data]);

  const toggle = (seg: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(seg)) next.delete(seg);
      else next.add(seg);
      return next;
    });

  const segments = segmentsQuery.data?.segments ?? [];
  const providers = syncStateQuery.data?.providers ?? [];

  const onSaveAndResync = async () => {
    setSyncPhase("running");
    setSyncMsg("");
    try {
      await saveSegments([...selected]);
      const res = (await sync(provider)) as {
        rows?: number;
        status?: string;
      };
      setSyncPhase("success");
      setSyncMsg(
        `Saved ${selected.size} segment(s) · re-sync ${res.status ?? "ok"}` +
          (typeof res.rows === "number" ? ` (${res.rows} rows)` : ""),
      );
    } catch (e) {
      setSyncPhase("error");
      setSyncMsg(e instanceof Error ? e.message : "sync failed");
    }
  };

  let body: React.ReactNode;
  if (segmentsQuery.isLoading) {
    body = <AsyncStateView status="loading" loadingLabel="Loading catalog status…" />;
  } else if (segmentsQuery.isError) {
    body = (
      <AsyncStateView
        status="error"
        error={segmentsQuery.error as ApiError}
        onRetry={() => segmentsQuery.refetch()}
      />
    );
  } else {
    body = (
      <>
        <div className="card">
          <h3>Source / Sync status</h3>
          {providers.length === 0 ? (
            <span className="muted">No provider sync state available.</span>
          ) : (
            <table className="table table-compact">
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>Status</th>
                  <th>Last sync</th>
                  <th className="num">Rows</th>
                  <th>Error</th>
                </tr>
              </thead>
              <tbody>
                {providers.map((p) => (
                  <tr key={p.provider ?? "unknown"}>
                    <td>{String(p.provider ?? "unknown")}</td>
                    <td>{String(p.status ?? "—")}</td>
                    <td className="muted">{String(p.last_sync ?? "—")}</td>
                    <td className="num">{String(p.rows ?? "—")}</td>
                    <td className="muted">{String(p.error ?? "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="card">
          <h3>Segments</h3>
          <p className="hint">
            Enable the market segments included in the catalog. Saving triggers a re-sync — this may
            change catalog contents. Saved preferences are preserved across restart.
          </p>
          <div className="segment-grid">
            {segments.map((s) => (
              <label key={s.segment} className="segment-item">
                <input
                  type="checkbox"
                  checked={selected.has(s.segment)}
                  onChange={() => toggle(s.segment)}
                />
                <span>
                  {s.segment}
                  {s.default ? " (default)" : ""}
                </span>
                <span className="muted">{s.catalog_rows} rows</span>
              </label>
            ))}
          </div>
          <div className="control-row" style={{ marginTop: 12 }}>
            <Field label="Sync provider">
              <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
                <option value="upstox">Upstox</option>
                <option value="fyers">Fyers</option>
              </Select>
            </Field>
            <Button variant="primary" onClick={onSaveAndResync}>
              Save &amp; Re-sync
            </Button>
            {syncPhase !== "idle" && (
              <span
                className={
                  syncPhase === "running"
                    ? "muted"
                    : syncPhase === "success"
                      ? "hint ok"
                      : "hint err"
                }
              >
                {syncPhase === "running" ? "Working…" : syncMsg}
              </span>
            )}
          </div>
        </div>
      </>
    );
  }

  return (
    <div className="panel instruments-view">
      <div className="page-header">
        <h1 className="page-title">Instruments</h1>
        <span className="muted">Catalog status · segment preferences · master sync</span>
      </div>
      {body}
    </div>
  );
}
