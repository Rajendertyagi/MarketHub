import { useEffect, useState } from "react";
import { AsyncStateView, Button, Field, Input } from "@/components/ui";
import type { ApiError } from "@/types";
import { useAppSettings, useSaveAppSettings } from "../useSettings";

export function GeneralPanel() {
  const { data, status, error, refetch } = useAppSettings();
  const save = useSaveAppSettings();
  const [baseUrl, setBaseUrl] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  useEffect(() => {
    if (data) setBaseUrl(data.public_base_url ?? "");
  }, [data]);

  if (status === "pending") {
    return <AsyncStateView status="loading" loadingLabel="Loading application settings…" />;
  }
  if (status === "error") {
    return <AsyncStateView status="error" error={error as ApiError} onRetry={() => refetch()} />;
  }

  async function onSave() {
    setMsg(null);
    if (!baseUrl.trim()) {
      setMsg({ kind: "err", text: "Public Base URL is required." });
      return;
    }
    try {
      await save.mutateAsync(baseUrl.trim());
      setMsg({ kind: "ok", text: "Saved. Restart MarketHub to apply." });
    } catch (e) {
      setMsg({
        kind: "err",
        text: e instanceof Error ? e.message : "Failed to save application settings.",
      });
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>General</h2>
      </div>
      <div className="card">
        <div className="control-row">
          <Field label="Public Base URL">
            <Input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://your-host.example"
            />
          </Field>
          <Button variant="primary" onClick={onSave} disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
        <div className="auth-row">
          <span className="auth-label">Fyers Callback URL</span>
          <span className="setting-val">{data?.fyers_callback_url ?? "—"}</span>
        </div>
        {msg ? <p className={`hint ${msg.kind}`}>{msg.text}</p> : null}
      </div>
    </div>
  );
}
