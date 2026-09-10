import { useEffect, useState } from "react";
import { Button, Input } from "@/components/ui";
import { AsyncStateView } from "@/components/ui";
import { useChatStatus, useSaveChatConfig } from "../useSettings";
import type { ApiError } from "@/types";

export function AiMcpPanel() {
  const { data, status, error, refetch } = useChatStatus();
  const save = useSaveChatConfig();
  const [endpoint, setEndpoint] = useState("");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  useEffect(() => {
    if (data) {
      setEndpoint(data.endpoint ?? "");
      setModel(data.model ?? "");
    }
  }, [data]);

  if (status === "pending") {
    return <AsyncStateView status="loading" loadingLabel="Loading AI provider…" />;
  }
  if (status === "error") {
    return (
      <AsyncStateView status="error" error={error as ApiError} onRetry={() => refetch()} />
    );
  }

  async function onSave() {
    setMsg(null);
    if (!endpoint.trim() || !model.trim() || !key.trim()) {
      setMsg({ kind: "err", text: "Endpoint, model and API key are required." });
      return;
    }
    try {
      await save.mutateAsync({ endpoint: endpoint.trim(), model: model.trim(), api_key: key.trim() });
      setMsg({ kind: "ok", text: "AI provider saved. Chat is ready." });
      setKey("");
    } catch (e) {
      setMsg({
        kind: "err",
        text: e instanceof Error ? e.message : "Save failed.",
      });
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>AI Provider</h2>
      </div>
      <div className="auth-form">
        <p className="form-hint">
          OpenAI-compatible endpoint for Chat. API key is stored encrypted on this
          computer only.
        </p>
        <div className="auth-row">
          <label>Endpoint</label>
          <Input
            type="text"
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
            placeholder="https://api.openai.com/v1"
            autoComplete="off"
          />
        </div>
        <div className="auth-row">
          <label>Model</label>
          <Input
            type="text"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="gpt-4o-mini"
            autoComplete="off"
          />
        </div>
        <div className="auth-row">
          <label>API Key</label>
          <Input
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="Paste API key"
            autoComplete="new-password"
          />
        </div>
        <div className="auth-row">
          <label></label>
          <Button variant="primary" onClick={onSave} disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save AI Settings"}
          </Button>
        </div>
        {msg ? <p className={`hint ${msg.kind}`}>{msg.text}</p> : null}
      </div>
      <div className="panel panel-spaced">
        <div className="panel-header">
          <h2>MCP Tools</h2>
        </div>
        <p className="form-hint">
          The MCP tool registry reference lives on its own page.
        </p>
        <a className="btn" href="#/mcp">
          Open MCP Tools
        </a>
      </div>
    </div>
  );
}
