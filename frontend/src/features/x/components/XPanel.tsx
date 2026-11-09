import { useEffect, useState } from "react";
import { AsyncStateView, Button, Field, Input } from "@/components/ui";
import type { ApiError } from "@/types";
import { X_AUTH_STATE_LABELS } from "../constants";
import { useXCredStatus, useXMutations, useXPollConfig, useXStatus, useXTweets } from "../useX";

// X/Twitter settings section: credentials, poll config, poll/auth status,
// recent cached tweets. X rules are configured in the EXISTING Alerts UI
// (source=twitter rows) — no duplicate rule UI here. No posting, no dashboard.
export function XPanel() {
  const cred = useXCredStatus();
  const cfg = useXPollConfig();
  const status = useXStatus();
  const tweets = useXTweets(10);
  const m = useXMutations();

  const [authToken, setAuthToken] = useState("");
  const [ct0, setCt0] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [interval, setInterval] = useState(120);
  const [limit, setLimit] = useState(20);
  const [retention, setRetention] = useState(30);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const loaded = cfg.data;
  useEffect(() => {
    if (loaded) {
      setEnabled(loaded.enabled);
      setInterval(loaded.poll_interval_seconds);
      setLimit(loaded.default_limit);
      setRetention(loaded.retention_days);
    }
  }, [loaded]);

  if (cred.status === "pending" || cfg.status === "pending") {
    return <AsyncStateView status="loading" loadingLabel="Loading X/Twitter settings…" />;
  }
  if (cred.status === "error") {
    return (
      <AsyncStateView
        status="error"
        error={cred.error as ApiError}
        onRetry={() => cred.refetch()}
      />
    );
  }

  const authState = status.data?.auth?.state ?? cred.data?.auth_state ?? "not_configured";
  const authLabel = X_AUTH_STATE_LABELS[authState] ?? authState;

  async function onSaveCreds() {
    setMsg(null);
    if (!authToken.trim() || !ct0.trim()) {
      setMsg({ kind: "err", text: "auth_token and ct0 are both required." });
      return;
    }
    try {
      await m.saveCreds(authToken.trim(), ct0.trim());
      setAuthToken("");
      setCt0("");
      setMsg({ kind: "ok", text: "X credentials saved (encrypted)." });
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : "Save failed." });
    }
  }

  async function onDeleteCreds() {
    setMsg(null);
    try {
      await m.deleteCreds();
      setMsg({ kind: "ok", text: "X credentials removed." });
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : "Delete failed." });
    }
  }

  async function onTestConnection() {
    setMsg(null);
    try {
      const res = await m.testConnection();
      if (res.status === "ok") {
        setMsg({
          kind: "ok",
          text: `Connection test passed — ${res.state ?? "authenticated"}.`,
        });
      } else {
        setMsg({
          kind: "err",
          text: `Connection test: ${res.state ?? "failed"}${
            res.reason ? ` — ${res.reason}` : ""
          }. Check the cookies or retry.`,
        });
      }
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : "Test failed." });
    }
  }

  async function onSaveConfig() {
    setMsg(null);
    try {
      await m.saveConfig({
        enabled,
        poll_interval_seconds: interval,
        default_limit: limit,
        retention_days: retention,
      });
      setMsg({ kind: "ok", text: "X poll config saved." });
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : "Save failed." });
    }
  }

  return (
    <div className="panel-stack">
      <section className="panel">
        <h3>X / Twitter — session</h3>
        <p className="muted">
          Status: <strong>{authLabel}</strong>
          {status.data?.last_error ? ` — ${status.data.last_error}` : null}
        </p>
        <p className="muted">
          {cred.data?.configured
            ? "Cookies stored (encrypted). Values are never displayed."
            : "Not configured — paste auth_token + ct0 from a logged-in X session."}
        </p>
        <Field label="auth_token">
          <Input
            type="password"
            value={authToken}
            onChange={(e) => setAuthToken(e.target.value)}
            placeholder="auth_token cookie"
            autoComplete="off"
          />
        </Field>
        <Field label="ct0">
          <Input
            type="password"
            value={ct0}
            onChange={(e) => setCt0(e.target.value)}
            placeholder="ct0 cookie"
            autoComplete="off"
          />
        </Field>
        <div className="row">
          <Button onClick={onSaveCreds}>Save credentials</Button>
          <Button
            onClick={onTestConnection}
            disabled={m.testing || !cred.data?.configured}
            title={
              cred.data?.configured
                ? "One live session check (no polling, no stored data)"
                : "Save cookies first"
            }
          >
            {m.testing ? "Testing…" : "Test connection"}
          </Button>
          <Button onClick={onDeleteCreds}>Remove</Button>
        </div>
      </section>

      <section className="panel">
        <h3>Polling</h3>
        <Field label="Enabled">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        </Field>
        <Field label="Poll interval (seconds, min 60)">
          <Input
            type="number"
            value={interval}
            min={60}
            onChange={(e) => setInterval(Number(e.target.value))}
          />
        </Field>
        <Field label="Default limit (1–20)">
          <Input
            type="number"
            value={limit}
            min={1}
            max={20}
            onChange={(e) => setLimit(Number(e.target.value))}
          />
        </Field>
        <Field label="Retention (days)">
          <Input
            type="number"
            value={retention}
            min={1}
            onChange={(e) => setRetention(Number(e.target.value))}
          />
        </Field>
        <div className="row">
          <Button onClick={onSaveConfig}>Save poll config</Button>
        </div>
        <p className="muted">
          Last fetch: {status.data?.last_fetch_at ?? "never"}
          {" · "}new last cycle: {status.data?.last_new_count ?? 0}
          {" · "}cached: {status.data?.cached_tweets ?? 0}
        </p>
      </section>

      <section className="panel">
        <h3>X rules (alerts)</h3>
        <p className="muted">
          X rules are MarketHub alerts with source “twitter” — create them in Settings → Alerts
          (keyword, handle, AND/OR, min likes/reposts, one-shot/recurring). A tweet alone never
          fires; only a rule match enters the alert pipeline.
        </p>
      </section>

      <section className="panel">
        <h3>Recent cached tweets</h3>
        {(tweets.data ?? []).length === 0 ? (
          <p className="muted">No cached tweets yet — enable polling to fill the cache.</p>
        ) : (
          <ul className="tweet-list">
            {(tweets.data ?? []).map((t) => (
              <li key={t.id}>
                <strong>{t.handle ? `@${t.handle}` : "?"}</strong>
                {" — "}
                <span>{(t.text ?? "").slice(0, 160)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {msg ? <p className={msg.kind === "ok" ? "ok-text" : "err-text"}>{msg.text}</p> : null}
    </div>
  );
}
