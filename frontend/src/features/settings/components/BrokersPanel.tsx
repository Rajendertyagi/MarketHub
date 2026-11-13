import { useEffect, useId, useState } from "react";
import { Button, Input } from "@/components/ui";
import {
  clearFyersLoginResult,
  type FyersLoginResult,
  loginWithFyers,
  loginWithUpstox,
  readFyersLoginResult,
} from "../api";
import { BROKER_FYERS, BROKER_UPSTOX } from "../constants";
import {
  formatFyersAuthChip,
  formatOnOffChip,
  formatTimestamp,
  formatUpstoxAuthChip,
} from "../format";
import type { MarketSource } from "../types";
import {
  useDeleteUpstoxCredentials,
  useForgetFyersSession,
  useForgetUpstoxSession,
  useFyersFeedConfig,
  useFyersSettings,
  useSaveFyersCredentials,
  useSaveFyersFeedConfig,
  useSaveUpstoxCredentials,
  useSaveUpstoxFeedConfig,
  useSourcesStatus,
  useUpstoxAuthStatus,
  useUpstoxCredStatus,
  useUpstoxFeedConfig,
  useUpstoxTokenLogin,
} from "../useSettings";
import { SourceDetail } from "./SourceDetail";

function Chip({ label, cls }: { label: string; cls: string }) {
  return <span className={cls}>{label}</span>;
}

function UpstoxBroker({ source }: { source?: MarketSource }) {
  const auth = useUpstoxAuthStatus();
  const cred = useUpstoxCredStatus();
  const feed = useUpstoxFeedConfig();
  const saveCred = useSaveUpstoxCredentials();
  const delCred = useDeleteUpstoxCredentials();
  const saveFeed = useSaveUpstoxFeedConfig();
  const forget = useForgetUpstoxSession();
  const tokenLogin = useUpstoxTokenLogin();

  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [token, setToken] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const uid = useId();

  const status = auth.data ?? {};
  const authed = !!status.authenticated;
  const authChip = formatUpstoxAuthChip(status);
  const loginLabel = authed
    ? "Login with Upstox"
    : status.session_restored || status.session_persisted
      ? "Re-login with Upstox"
      : "Login with Upstox";
  const sessionNote = authed
    ? `Valid until ${status.expires_at ? formatTimestamp(status.expires_at) : "session active"}${
        status.session_restored || status.session_persisted ? " · auto-restored on restart" : ""
      }`
    : status.oauth_available
      ? "Login required — Upstox tokens expire 3:30 AM IST (one click per day)"
      : "Save your API Key & Secret above to enable login.";

  async function setMsgAsync(fn: () => Promise<unknown>, okText: string) {
    setMsg(null);
    setBusy(true);
    try {
      await fn();
      setMsg({ kind: "ok", text: okText });
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : "Request failed." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card broker-card">
      <div className="panel-header">
        <h2>Upstox</h2>
      </div>
      <div className="auth-form">
        <div className="auth-row">
          <span className="auth-label">Status</span>
          <div className="state-grid">
            <Chip label={authChip.label} cls={authChip.cls} />
            <Chip
              label={status.feed_configured ? "Feed Configured" : "No Feed Configured"}
              cls={formatOnOffChip(!!status.feed_configured).cls}
            />
            <Chip
              label={cred.data?.api_key_configured ? "Credentials" : "No Credentials"}
              cls={formatOnOffChip(!!cred.data?.api_key_configured).cls}
            />
            <Chip
              label={
                status.session_restored || status.session_persisted ? "Session Saved" : "No Session"
              }
              cls={formatOnOffChip(!!status.session_restored || !!status.session_persisted).cls}
            />
            <Chip
              label={status.login_required ? "Login Required" : "Logged In"}
              cls={formatOnOffChip(!status.login_required).cls}
            />
          </div>
        </div>
        <p className="hint session-note">{sessionNote}</p>

        <div className="auth-row">
          <label htmlFor={`${uid}-upstox-key`}>API Key</label>
          <Input
            id={`${uid}-upstox-key`}
            type="text"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="Paste API key"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <div className="auth-row">
          <label htmlFor={`${uid}-upstox-secret`}>API Secret</label>
          <Input
            id={`${uid}-upstox-secret`}
            type="password"
            value={apiSecret}
            onChange={(e) => setApiSecret(e.target.value)}
            placeholder="Paste API secret"
            autoComplete="new-password"
            spellCheck={false}
          />
        </div>
        <div className="auth-row">
          <span className="auth-label">Credentials</span>
          <Button
            className="btn btn-compact"
            onClick={() =>
              setMsgAsync(
                () => saveCred.mutateAsync({ apiKey, apiSecret }),
                "Credentials saved.",
              ).then(() => {
                setApiKey("");
                setApiSecret("");
              })
            }
            disabled={busy}
          >
            Save
          </Button>
          <Button
            className="btn btn-compact btn-outline-danger"
            onClick={() => setMsgAsync(() => delCred.mutateAsync(), "Credentials deleted.")}
            disabled={busy}
          >
            Delete
          </Button>
        </div>

        {!authed ? (
          <>
            <div className="auth-row">
              <span className="auth-label">Login</span>
              {status.oauth_available || cred.data?.api_key_configured ? (
                <Button
                  className="btn btn-compact"
                  onClick={() => loginWithUpstox()}
                  disabled={busy}
                >
                  {loginLabel}
                </Button>
              ) : (
                <span className="hint">
                  Save your API Key &amp; Secret above to enable OAuth login.
                </span>
              )}
            </div>
            <div className="auth-row">
              <label htmlFor={`${uid}-upstox-token`}>Manual Token</label>
              <Input
                id={`${uid}-upstox-token`}
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="Paste Upstox access token"
                autoComplete="off"
                spellCheck={false}
              />
              <Button
                className="btn btn-compact"
                onClick={() =>
                  setMsgAsync(
                    () => tokenLogin.mutateAsync(token),
                    "Token saved for this session.",
                  ).then(() => setToken(""))
                }
                disabled={busy}
              >
                Save Token
              </Button>
            </div>
          </>
        ) : (
          <div className="auth-row">
            <span className="auth-label">Session</span>
            <Button
              className="btn btn-compact btn-outline-danger"
              onClick={() => setMsgAsync(() => forget.mutateAsync(), "Logged out of Upstox.")}
              disabled={busy}
            >
              Logout
            </Button>
          </div>
        )}

        <SourceDetail
          source={source}
          feedEnabled={feed.data?.enabled}
          onFeedToggle={() =>
            feed.data &&
            setMsgAsync(() => saveFeed.mutateAsync(!feed.data.enabled), "Feed updated.")
          }
          feedBusy={busy}
        />

        {msg ? <p className={`hint ${msg.kind}`}>{msg.text}</p> : null}
      </div>
    </section>
  );
}

// Safe, static login-result copy (no tokens, secrets, or callback payloads).
const FYERS_LOGIN_COPY: Record<FyersLoginResult, { kind: "ok" | "err"; text: string }> = {
  ok: { kind: "ok", text: "Fyers login successful — session connected." },
  rejected: {
    kind: "err",
    text: "Fyers login failed — Fyers rejected the authorization. Check the App ID/secret, use a fresh login (codes are single-use), and retry.",
  },
  retry: {
    kind: "err",
    text: "Fyers login incomplete — no authorization was received. Please try again.",
  },
  expired: {
    kind: "err",
    text: "Fyers login session expired before completing — please try again.",
  },
  error: {
    kind: "err",
    text: "Fyers login failed — the session could not be saved. You can retry directly; no need to forget anything first.",
  },
};

function FyersBroker({ source }: { source?: MarketSource }) {
  const settings = useFyersSettings();
  const feedConfig = useFyersFeedConfig();
  const saveCred = useSaveFyersCredentials();
  const forget = useForgetFyersSession();
  const saveFeed = useSaveFyersFeedConfig();

  const [appId, setAppId] = useState("");
  const [secret, setSecret] = useState("");
  const [pin, setPin] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [feedEnabled, setFeedEnabled] = useState(false);
  const [tbtEnabled, setTbtEnabled] = useState(false);
  const [tbtSymbols, setTbtSymbols] = useState("");
  // Login result flag from the backend callback redirect (?fyers_auth=…).
  const [loginResult, setLoginResult] = useState<FyersLoginResult | null>(() =>
    readFyersLoginResult(),
  );
  const uid = useId();

  const s = settings.data ?? {};
  const loading = settings.isLoading;
  const credsOk = !!s.app_id_configured && !!s.secret_configured;
  // Canonical gate: backend auth state, never token presence. Unknown and
  // loading are never treated as authenticated, so a stale token can never
  // hide Login/Re-login.
  const authed = !loading && s.authenticated === true;
  const loginLabel =
    s.session_restored || s.session_persisted ? "Re-login with Fyers" : "Login with Fyers";
  // Forget Session is an independent recovery control: visible whenever
  // session material exists, and it never gates Login.
  const sessionExists = !!s.access_token_active || !!s.session_persisted || !!s.restart_recovery;
  const authChip = loading
    ? { label: "Checking session…", cls: "chip chip-off" }
    : formatFyersAuthChip(s);
  const resultCopy = loginResult ? FYERS_LOGIN_COPY[loginResult] : null;

  useEffect(() => {
    const fc = feedConfig.data;
    if (!fc) return;
    setFeedEnabled(!!fc.enabled);
    setTbtEnabled(!!fc.tbt_enabled);
    setTbtSymbols((fc.tbt_symbols ?? []).join("\n"));
  }, [feedConfig.data]);

  function dismissLoginResult() {
    setLoginResult(null);
    clearFyersLoginResult();
  }

  async function setMsgAsync(fn: () => Promise<unknown>, okText: string) {
    setMsg(null);
    setBusy(true);
    try {
      await fn();
      setMsg({ kind: "ok", text: okText });
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof Error ? e.message : "Request failed." });
    } finally {
      setBusy(false);
    }
  }

  function parseTbtSymbols(input: string): string[] {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const part of input.replace(/\r/g, "\n").replace(/,/g, "\n").split("\n")) {
      const sym = part.trim();
      if (!sym || seen.has(sym)) continue;
      seen.add(sym);
      out.push(sym);
    }
    return out;
  }

  return (
    <section className="card broker-card">
      <div className="panel-header">
        <h2>Fyers</h2>
      </div>
      <div className="auth-form">
        {resultCopy ? (
          <div className="auth-row">
            <span className="auth-label">Login result</span>
            <p className={`hint ${resultCopy.kind}`}>
              {resultCopy.text}{" "}
              <Button className="btn btn-compact" onClick={dismissLoginResult}>
                Dismiss
              </Button>
            </p>
          </div>
        ) : null}
        <div className="auth-row">
          <span className="auth-label">Status</span>
          <div className="state-grid">
            <Chip label={authChip.label} cls={authChip.cls} />
            <Chip
              label={credsOk ? "Credentials" : "No Credentials"}
              cls={formatOnOffChip(credsOk).cls}
            />
            <Chip
              label={s.login_available ? "Login Ready" : "Credentials Required"}
              cls={formatOnOffChip(!!s.login_available).cls}
            />
            <Chip
              label={s.session_persisted ? "Session Saved" : "No Session"}
              cls={formatOnOffChip(!!s.session_persisted).cls}
            />
            <Chip
              label={s.login_required ? "Login Required" : "Connected"}
              cls={formatOnOffChip(!s.login_required).cls}
            />
          </div>
        </div>

        <div className="auth-row">
          <label htmlFor={`${uid}-fyers-appid`}>App ID</label>
          <Input
            id={`${uid}-fyers-appid`}
            type="text"
            value={appId}
            onChange={(e) => setAppId(e.target.value)}
            placeholder="Paste Fyers app id"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <div className="auth-row">
          <label htmlFor={`${uid}-fyers-secret`}>Secret Key</label>
          <Input
            id={`${uid}-fyers-secret`}
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder="Paste secret key"
            autoComplete="new-password"
            spellCheck={false}
          />
        </div>
        <div className="auth-row">
          <label htmlFor={`${uid}-fyers-pin`}>Account PIN</label>
          <Input
            id={`${uid}-fyers-pin`}
            type="password"
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            placeholder="PIN (enables auto session restore)"
            autoComplete="new-password"
            spellCheck={false}
          />
        </div>
        <div className="auth-row">
          <span className="auth-label">Credentials</span>
          <Button
            className="btn btn-compact"
            onClick={() =>
              setMsgAsync(
                () => saveCred.mutateAsync({ appId, secretId: secret, pin }),
                "Fyers credentials saved.",
              ).then(() => {
                setAppId("");
                setSecret("");
                setPin("");
              })
            }
            disabled={busy}
          >
            Save
          </Button>
        </div>

        {!authed ? (
          <div className="auth-row">
            <span className="auth-label">Login</span>
            <Button
              className="btn btn-compact"
              onClick={() => loginWithFyers()}
              disabled={busy || loading}
            >
              {loginLabel}
            </Button>
            {!s.login_available ? (
              <span className="hint">
                Save your App ID, Secret &amp; PIN above to enable login.
              </span>
            ) : null}
          </div>
        ) : (
          <div className="auth-row">
            <span className="auth-label">Session</span>
            <span className="hint">
              Connected
              {s.access_token_expires_at
                ? ` · valid until ${formatTimestamp(s.access_token_expires_at)}`
                : ""}
              {s.session_restored || s.session_persisted ? " · auto-restored on restart" : ""}
            </span>
          </div>
        )}

        {sessionExists ? (
          <div className="auth-row">
            <span className="auth-label">Recovery</span>
            <Button
              className="btn btn-compact btn-outline-danger"
              onClick={() =>
                setMsgAsync(() => forget.mutateAsync(), "Saved Fyers session forgotten.")
              }
              disabled={busy}
            >
              Forget Session
            </Button>
            <span className="hint">
              Clears the saved session (keeps credentials). Never required before login.
            </span>
          </div>
        ) : null}

        <SourceDetail source={source} />

        <div className="auth-row">
          <span className="auth-label">Feed</span>
          <label className="hint" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input
              type="checkbox"
              checked={feedEnabled}
              onChange={(e) => setFeedEnabled(e.target.checked)}
              disabled={busy}
            />
            Enable Fyers market data feed
          </label>
        </div>

        <div className="auth-row">
          <span className="auth-label">TBT Depth</span>
          <label className="hint" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <input
              type="checkbox"
              checked={tbtEnabled}
              onChange={(e) => setTbtEnabled(e.target.checked)}
              disabled={busy}
            />
            Enable Tick-by-Tick 50-level depth (NSE Equity &amp; NFO only)
          </label>
        </div>

        <div className="auth-row">
          <label htmlFor={`${uid}-fyers-tbt-symbols`}>TBT Symbols</label>
          <textarea
            id={`${uid}-fyers-tbt-symbols`}
            value={tbtSymbols}
            onChange={(e) => setTbtSymbols(e.target.value)}
            placeholder={"One exact FYERS provider symbol per line, e.g.\nNSE:RELIANCE-EQ"}
            rows={3}
            disabled={busy || !tbtEnabled}
            spellCheck={false}
            style={{ width: "100%", fontFamily: "inherit" }}
          />
          <span className="hint">
            Optional. Exact provider symbols, validated against catalog metadata at runtime. Standard plan: 1 symbol.
            Leave empty for deterministic fallback from configured instruments (eligible NSE Equity/NFO only). Changes apply on save (feed may restart if running).
          </span>
        </div>

        <div className="auth-row">
          <span className="auth-label">Feed Config</span>
          <Button
            className="btn btn-compact"
            onClick={() =>
              setMsgAsync(
                () =>
                  saveFeed.mutateAsync({
                    enabled: feedEnabled,
                    tbt_enabled: tbtEnabled,
                    tbt_symbols: parseTbtSymbols(tbtSymbols),
                  }),
                "Fyers feed settings saved.",
              )
            }
            disabled={busy}
          >
            Save Feed Settings
          </Button>
        </div>

        {msg ? <p className={`hint ${msg.kind}`}>{msg.text}</p> : null}
      </div>
    </section>
  );
}

export function BrokersPanel() {
  const { data: sourcesData } = useSourcesStatus();
  const sources = sourcesData?.sources ?? [];
  const upstoxSource = sources.find((s) => s.name === BROKER_UPSTOX);
  const fyersSource = sources.find((s) => s.name === BROKER_FYERS);
  return (
    <div className="brokers-list">
      <UpstoxBroker source={upstoxSource} />
      <FyersBroker source={fyersSource} />
    </div>
  );
}
