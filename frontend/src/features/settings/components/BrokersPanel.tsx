import { useId, useState } from "react";
import { Button, Input } from "@/components/ui";
import { loginWithFyers, loginWithUpstox } from "../api";
import { BROKER_FYERS, BROKER_UPSTOX } from "../constants";
import { formatOnOffChip, formatTimestamp, formatUpstoxAuthChip } from "../format";
import type { MarketSource } from "../types";
import {
  useDeleteUpstoxCredentials,
  useForgetFyersSession,
  useForgetUpstoxSession,
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

function FyersBroker({ source }: { source?: MarketSource }) {
  const settings = useFyersSettings();
  const saveCred = useSaveFyersCredentials();
  const forget = useForgetFyersSession();
  const saveFeed = useSaveFyersFeedConfig();

  const [appId, setAppId] = useState("");
  const [secret, setSecret] = useState("");
  const [pin, setPin] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const uid = useId();

  const s = settings.data ?? {};
  const credsOk = !!s.app_id_configured && !!s.secret_configured;
  const loggedIn = !!s.access_token_active;

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
        <h2>Fyers</h2>
      </div>
      <div className="auth-form">
        <div className="auth-row">
          <span className="auth-label">Status</span>
          <div className="state-grid">
            <Chip
              label={credsOk ? "Credentials" : "No Credentials"}
              cls={formatOnOffChip(credsOk).cls}
            />
            <Chip
              label={s.login_available ? "Login Ready" : "Credentials Required"}
              cls={formatOnOffChip(!!s.login_available).cls}
            />
            <Chip
              label={loggedIn ? "Logged In" : "Login Required"}
              cls={formatOnOffChip(loggedIn).cls}
            />
            <Chip
              label={s.session_persisted ? "Session Saved" : "No Session"}
              cls={formatOnOffChip(!!s.session_persisted).cls}
            />
            <Chip
              label={s.login_required ? "Login Required" : "Logged In"}
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

        {!loggedIn ? (
          <div className="auth-row">
            <span className="auth-label">Login</span>
            <Button className="btn btn-compact" onClick={() => loginWithFyers()} disabled={busy}>
              Login with Fyers
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
            <Button
              className="btn btn-compact btn-outline-danger"
              onClick={() =>
                setMsgAsync(() => forget.mutateAsync(), "Saved Fyers session forgotten.")
              }
              disabled={busy}
            >
              Forget Session
            </Button>
          </div>
        )}

        <SourceDetail
          source={source}
          feedEnabled={s.source_enabled}
          onFeedToggle={() =>
            setMsgAsync(() => saveFeed.mutateAsync(!s.source_enabled), "Feed updated.")
          }
          feedBusy={busy}
        />

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
