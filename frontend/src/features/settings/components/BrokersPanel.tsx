import { useState } from "react";
import { Button, Input } from "@/components/ui";
import {
  useDeleteUpstoxCredentials,
  useForgetFyersSession,
  useForgetUpstoxSession,
  useSaveFyersCredentials,
  useSaveFyersFeedConfig,
  useSaveUpstoxCredentials,
  useSaveUpstoxFeedConfig,
  useUpstoxAuthStatus,
  useUpstoxCredStatus,
  useUpstoxFeedConfig,
  useFyersSettings,
  useSourceControl,
} from "../useSettings";
import {
  loginWithFyers,
  loginWithUpstox,
} from "../api";
import {
  formatOnOffChip,
  formatUpstoxAuthChip,
} from "../format";
import { BROKER_FYERS, BROKER_UPSTOX } from "../constants";
import { useUpstoxPinLogin, useUpstoxTokenLogin } from "../useSettings";

function Chip({ label, cls }: { label: string; cls: string }) {
  return <span className={cls}>{label}</span>;
}

function UpstoxBroker() {
  const auth = useUpstoxAuthStatus();
  const cred = useUpstoxCredStatus();
  const feed = useUpstoxFeedConfig();
  const saveCred = useSaveUpstoxCredentials();
  const delCred = useDeleteUpstoxCredentials();
  const saveFeed = useSaveUpstoxFeedConfig();
  const forget = useForgetUpstoxSession();
  const tokenLogin = useUpstoxTokenLogin();
  const pinLogin = useUpstoxPinLogin();
  const { restart } = useSourceControl();

  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [token, setToken] = useState("");
  const [pin, setPin] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const status = auth.data ?? {};
  const authChip = formatUpstoxAuthChip(status);
  const showLogin =
    !!status.oauth_available &&
    status.login_required !== false &&
    !status.authenticated;
  const showPin = !!status.auth_code_pending;

  async function setMsgAsync(fn: () => Promise<unknown>, okText: string) {
    setMsg(null);
    setBusy(true);
    try {
      await fn();
      setMsg({ kind: "ok", text: okText });
    } catch (e) {
      setMsg({
        kind: "err",
        text: e instanceof Error ? e.message : "Request failed.",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel panel-spaced">
      <div className="panel-header">
        <h2>Upstox</h2>
      </div>
      <div className="auth-form">
        <div className="auth-row">
          <label>Auth Status</label>
          <Chip label={authChip.label} cls={authChip.cls} />
        </div>
        <div className="auth-row">
          <label>Feed State</label>
          <span className="setting-val">{status.state ?? "—"}</span>
        </div>

        {showLogin ? (
          <div className="auth-row">
            <label></label>
            <Button onClick={() => loginWithUpstox(false)} disabled={busy}>
              Login with Upstox
            </Button>
          </div>
        ) : null}

        <div className="auth-row auth-row-spaced">
          <label>Upstox Feed</label>
          <Button
            onClick={() => feed.data && saveFeed.mutateAsync(!feed.data.enabled)}
            disabled={busy}
          >
            {feed.data?.enabled ? "Disable Feed" : "Enable Feed"}
          </Button>
          <Chip
            label={feed.data?.enabled ? "Enabled" : "Disabled"}
            cls={feed.data?.enabled ? "chip chip-on" : "chip chip-off"}
          />
        </div>

        <div className="auth-row">
          <label></label>
          <div className="state-grid">
            <Chip
              label={status.feed_configured ? "Configured" : "Not Configured"}
              cls={formatOnOffChip(!!status.feed_configured).cls}
            />
            <Chip
              label={
                cred.data?.api_key_configured
                  ? "Credentials Saved"
                  : "No Credentials"
              }
              cls={formatOnOffChip(!!cred.data?.api_key_configured).cls}
            />
            <Chip
              label={status.authenticated ? "Authenticated" : "Not Authenticated"}
              cls={formatOnOffChip(!!status.authenticated).cls}
            />
            <Chip
              label={
                status.session_restored
                  ? "Session Restored"
                  : status.session_persisted
                    ? "Session Saved"
                    : "No Session Saved"
              }
              cls={
                formatOnOffChip(
                  !!status.session_restored || !!status.session_persisted,
                ).cls
              }
            />
            <Chip
              label={status.restart_recovery ? "Restart Recovery On" : "Restart Recovery Off"}
              cls={formatOnOffChip(!!status.restart_recovery).cls}
            />
            <Chip
              label={status.login_required ? "Login Required" : "No Login Needed"}
              cls={formatOnOffChip(!status.login_required).cls}
            />
          </div>
        </div>

        <div className="auth-row">
          <label>API Key</label>
          <Input
            type="text"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="Paste API key"
            autoComplete="off"
          />
        </div>
        <div className="auth-row">
          <label>API Secret</label>
          <Input
            type="password"
            value={apiSecret}
            onChange={(e) => setApiSecret(e.target.value)}
            placeholder="Paste API secret"
            autoComplete="new-password"
          />
        </div>
        <div className="auth-row">
          <label></label>
          <Button
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
            Save Credentials
          </Button>
          <Button
            className="btn btn-outline-danger"
            onClick={() =>
              setMsgAsync(() => delCred.mutateAsync(), "Credentials deleted.")
            }
            disabled={busy}
          >
            Delete Credentials
          </Button>
        </div>

        <details className="auth-advanced">
          <summary>Advanced / Recovery</summary>
          {showPin ? (
            <div className="auth-row">
              <label>Daily PIN Login</label>
              <Input
                type="password"
                value={pin}
                onChange={(e) => setPin(e.target.value)}
                placeholder="Upstox PIN"
                autoComplete="off"
              />
              <Button
                onClick={() =>
                  setMsgAsync(
                    () => pinLogin.mutateAsync(pin),
                    "Upstox login successful.",
                  ).then(() => setPin(""))
                }
                disabled={busy}
              >
                Login with PIN
              </Button>
            </div>
          ) : null}
          <div className="auth-row">
            <label>Manual Token</label>
            <Input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Paste Upstox access token"
              autoComplete="off"
            />
          </div>
          <div className="auth-row">
            <label></label>
            <Button
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
        </details>

        <div className="auth-row">
          <label>Session &amp; Restart</label>
          <Button
            className="btn btn-outline-danger"
            onClick={() =>
              setMsgAsync(
                () => forget.mutateAsync(),
                "Logged out of Upstox.",
              )
            }
            disabled={busy || !status.authenticated}
          >
            Logout
          </Button>
          <Button
            onClick={() =>
              setMsgAsync(
                () => restart(BROKER_UPSTOX),
                "Feed reconnect triggered.",
              )
            }
            disabled={busy}
          >
            Reconnect Feed
          </Button>
        </div>
        {msg ? <p className={`hint ${msg.kind}`}>{msg.text}</p> : null}
      </div>
    </div>
  );
}

function FyersBroker() {
  const settings = useFyersSettings();
  const saveCred = useSaveFyersCredentials();
  const forget = useForgetFyersSession();
  const saveFeed = useSaveFyersFeedConfig();
  const { restart } = useSourceControl();

  const [appId, setAppId] = useState("");
  const [secret, setSecret] = useState("");
  const [pin, setPin] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const s = settings.data ?? {};
  const credsOk = !!s.app_id_configured && !!s.secret_configured;

  async function setMsgAsync(fn: () => Promise<unknown>, okText: string) {
    setMsg(null);
    setBusy(true);
    try {
      await fn();
      setMsg({ kind: "ok", text: okText });
    } catch (e) {
      setMsg({
        kind: "err",
        text: e instanceof Error ? e.message : "Request failed.",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel panel-spaced">
      <div className="panel-header">
        <h2>Fyers</h2>
      </div>
      <div className="auth-form">
        <p className="form-hint">One-time setup. Saved encrypted on this computer only.</p>
        <div className="auth-row">
          <label>App Credentials</label>
          <Chip
            label={credsOk ? "Configured" : "Not configured"}
            cls={formatOnOffChip(credsOk).cls}
          />
        </div>
        <div className="auth-row">
          <label>Daily Login</label>
          <Chip
            label={
              !s.login_available
                ? "Credentials Required"
                : s.access_token_active
                  ? "Daily Login Active"
                  : "Login Required"
            }
            cls={formatOnOffChip(!!s.access_token_active).cls}
          />
        </div>

        {s.login_available ? (
          <div className="auth-row">
            <label></label>
            <Button onClick={() => loginWithFyers()} disabled={busy}>
              Login with Fyers
            </Button>
          </div>
        ) : null}

        <div className="auth-row auth-row-spaced">
          <label>Fyers Feed</label>
          <Button
            onClick={() => saveFeed.mutateAsync(!s.source_enabled)}
            disabled={busy}
          >
            {s.source_enabled ? "Disable Feed" : "Enable Feed"}
          </Button>
          <Chip
            label={s.source_enabled ? "Enabled" : "Disabled"}
            cls={s.source_enabled ? "chip chip-on" : "chip chip-off"}
          />
        </div>

        <div className="auth-row">
          <label>App ID</label>
          <Input
            type="text"
            value={appId}
            onChange={(e) => setAppId(e.target.value)}
            placeholder="Paste Fyers app id"
            autoComplete="off"
          />
        </div>
        <div className="auth-row">
          <label>Secret Key</label>
          <Input
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder="Paste secret key"
            autoComplete="new-password"
          />
        </div>
        <div className="auth-row">
          <label>Account PIN</label>
          <Input
            type="password"
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            placeholder="PIN (enables auto session restore)"
            autoComplete="new-password"
          />
        </div>
        <div className="auth-row">
          <label></label>
          <Button
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
            Save Fyers Credentials
          </Button>
        </div>

        <div className="auth-row">
          <label>Session &amp; Restart</label>
          <Button
            className="btn btn-outline-danger"
            onClick={() =>
              setMsgAsync(() => forget.mutateAsync(), "Saved Fyers session forgotten.")
            }
            disabled={busy}
          >
            Forget Saved Session
          </Button>
          <Button
            onClick={() =>
              setMsgAsync(
                () => restart(BROKER_FYERS),
                "Feed reconnect triggered.",
              )
            }
            disabled={busy}
          >
            Reconnect Feed
          </Button>
        </div>
        {msg ? <p className={`hint ${msg.kind}`}>{msg.text}</p> : null}
      </div>
    </div>
  );
}

export function BrokersPanel() {
  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Brokers</h2>
      </div>
      <p className="form-hint">
        Upstox and Fyers connection management. Credentials travel only in POST
        bodies and are cleared after a successful save.
      </p>
      <UpstoxBroker />
      <FyersBroker />
    </div>
  );
}
