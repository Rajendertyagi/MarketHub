/**
 * MarketHub WebUI — Upstox auth + app credential UI.
 *
 * Owns the access-token submission form, OAuth login buttons, the daily
 * auth snapshot (shared read-only with market-sources), and the Settings
 * credential CRUD. Security behavior is preserved verbatim:
 * - tokens/credentials travel only in POST JSON bodies (never URL)
 * - nothing is written to localStorage
 * - secret fields are cleared immediately after a successful save
 * - OAuth callback params are stripped from browser history
 */

import { $ } from "./utils.js";
import { switchView } from "./router.js";
import { friendlyState, pollSources } from "./market-sources.js";

let lastAuthStatus = null;   // /api/auth/upstox/status snapshot

/** Read-only daily-auth snapshot for market-sources controls. */
export function getAuthStatus() {
  return lastAuthStatus;
}

/** Set a state-chip's label + on/off/warn class (idempotent). */
function setChip(id, label, cls) {
  const el = $(id);
  if (!el) return;
  el.textContent = label;
  el.className = "state-chip" + (cls ? " " + cls : "");
}

// ── Upstox auth (token submit) ──────────────────────────────────────────

export async function pollAuthStatus() {
  try {
    const res = await fetch("/api/auth/upstox/status");
    const d = await res.json();
    lastAuthStatus = d;   // consumed by the Sources page controls
    const chip = $("auth-token-status");
    const loginBtn = $("oauth-login-btn");
    const pinLoginBtn = $("oauth-login-pin-btn");
    const pinRow = $("upstox-pin-row");
    // Login UI is offered once Upstox API credentials are configured. The
    // auth code may already be pending (PIN-mode), in which case surface the
    // PIN field regardless of credential state.
    const ready = d.oauth_available;
    if (loginBtn) {
      loginBtn.classList.toggle("hidden", !ready);
      if (ready) {
        loginBtn.disabled = d.auth_state === "authorizing";
        loginBtn.textContent = d.token_configured
          ? "Login with Upstox (renew)" : "Login with Upstox";
      }
    }
    if (pinLoginBtn) {
      pinLoginBtn.classList.toggle("hidden", !ready);
    }
    if (pinRow) {
      pinRow.classList.toggle("hidden", !d.auth_code_pending);
    }
    let label, cls;
    if (!d.oauth_available) {
      label = "Credentials Missing"; cls = "chip chip-off";
    } else if (!d.token_configured || d.expired === true
               || d.state === "auth_required") {
      label = "Daily Login Required"; cls = "chip chip-off";
    } else if (d.expiry_known) {
      label = "Active"; cls = "chip chip-on";
    } else {
      label = "Configured"; cls = "chip chip-on";
    }
    chip.textContent = label;
    chip.className = cls;
    // Feed runtime state is a SEPARATE concept from daily auth.
    let feedLabel = d.state || "—";
    if (feedLabel === "auth_required") feedLabel = "Stopped (login required)";
    $("auth-feed-state").textContent = feedLabel;
    // Feed configuration + runtime status are distinct from broker/auth state.
    pollUpstoxFeed();

    // ── Broker state grid: eight independent real states (never one bool) ──
    setChip("upx-configured",
      d.feed_configured ? "Configured" : "Not Configured",
      d.feed_configured ? "is-on" : "is-off");
    setChip("upx-creds",
      d.oauth_available ? "Credentials Saved" : "No Credentials",
      d.oauth_available ? "is-on" : "is-off");
    const upxAuthed = !!d.token_configured && d.expired !== true
      && d.state !== "auth_required";
    setChip("upx-auth",
      upxAuthed ? "Authenticated" : "Not Authenticated",
      upxAuthed ? "is-on" : "is-off");
    setChip("upx-restored",
      d.session_restored ? "Session Restored" : "No Session Restore",
      d.session_restored ? "is-on" : "is-off");
    const upxRunning = !!d.configured && d.state
      && !["stopped", "auth_required"].includes(d.state);
    setChip("upx-feed",
      upxRunning ? "Feed Active" : "Feed Inactive",
      upxRunning ? "is-on" : "is-off");
    if (d.expires_at) {
      setChip("upx-expiry", "Expiry " + new Date(d.expires_at).toLocaleString(),
        "is-warn");
    } else if (d.expiry_known && d.expired) {
      setChip("upx-expiry", "Expired", "is-off");
    } else {
      setChip("upx-expiry", "Expiry —", "");
    }
    setChip("upx-recovery",
      d.restart_recovery ? "Restart Recovery On" : "Restart Recovery Off",
      d.restart_recovery ? "is-on" : "is-off");
    setChip("upx-loginreq",
      d.login_required ? "Login Required" : "No Login Needed",
      d.login_required ? "is-off" : "is-on");
  } catch { /* silent */ }
}

// Reflect the Upstox feed's four independent states in the Brokers UI:
//   feed configured (durable config) / enabled / authenticated / runtime active.
// These are NEVER collapsed into a single boolean.
async function pollUpstoxFeed() {
  try {
    const [fRes, sRes] = await Promise.all([
      fetch("/api/settings/upstox/feed"),
      fetch("/api/auth/upstox/status"),
    ]);
    const f = await fRes.json();
    const s = await sRes.json();

    const toggle = $("upstox-feed-toggle");
    const chip = $("upstox-feed-chip");
    const cfgChip = $("upstox-feed-configured");
    const authChip = $("upstox-auth-chip");
    const rtChip = $("upstox-feed-runtime");
    const msg = $("upstox-feed-msg");

    if (toggle) {
      toggle.textContent = f.enabled ? "Disable Feed" : "Enable Feed";
      toggle.classList.toggle("btn-outline-danger", !!f.enabled);
    }
    if (chip) {
      chip.textContent = f.enabled ? "Enabled" : "Disabled";
      chip.className = "chip " + (f.enabled ? "chip-on" : "chip-off");
    }
    if (cfgChip) {
      cfgChip.textContent = f.configured ? "Configured" : "Not configured";
      cfgChip.className = "chip " + (f.configured ? "chip-on" : "chip-off");
    }
    if (authChip) {
      const authed = !!s.token_configured;
      authChip.textContent = authed ? "Authenticated" : "Not authenticated";
      authChip.className = "chip " + (authed ? "chip-on" : "chip-off");
    }
    if (rtChip) {
      const running = !!s.configured && s.state
        && !["stopped", "auth_required"].includes(s.state);
      rtChip.textContent = running ? "Running" : "Not running";
      rtChip.className = "chip " + (running ? "chip-on" : "chip-off");
    }
    // Surface a restart requirement only when not already shown.
    if (msg && !msg.textContent.trim() && f.restart_required) {
      msg.textContent = "Feed configuration saved — restart MarketHub to apply.";
      msg.className = "hint";
    }
  } catch { /* silent */ }
}

function handleAuthCallbackParam() {
  const params = new URLSearchParams(window.location.search);
  const auth = params.get("auth");
  const fyersAuth = params.get("fyers_auth");
  if (!auth && !fyersAuth) return;

  const hashView = location.hash.startsWith("#/")
    ? location.hash.slice(2) : "settings";
  if (document.getElementById("view-" + hashView)) {
    switchView(hashView);
  } else {
    switchView("settings");
  }

  const msg = fyersAuth ? $("fyers-message") : $("auth-message");
  if (fyersAuth) {
    if (fyersAuth === "ok") {
      msg.textContent = "Fyers authentication successful.";
      msg.className = "hint ok";
    } else {
      msg.textContent = "Fyers authentication failed. Check App ID/Secret and try Login again.";
      msg.className = "hint err";
    }
  } else if (auth === "ok") {
    msg.textContent = "Upstox authentication successful. Connecting market feed…";
    msg.className = "hint ok";
  } else if (auth === "pin_required") {
    msg.textContent = "Upstox authorization received — enter your Upstox PIN to complete login.";
    msg.className = "hint ok";
    const pinInput = $("upstox-pin");
    if (pinInput) pinInput.focus();
  } else if (auth === "failed") {
    const reason = params.get("reason");
    let text;
    if (reason === "rejected") {
      text = "Upstox rejected the login. Check in Settings that your API Key and Secret are correct, and that the Redirect URL in your Upstox developer app is EXACTLY: " + window.location.origin + "/auth/upstox/callback";
    } else if (reason === "expired") {
      text = "The login session expired (10 minutes). Click Login with Upstox again.";
    } else if (reason === "retry") {
      text = "Login session invalid — possibly an old tab or double-click. Click Login with Upstox again.";
    } else if (reason === "network") {
      text = "Could not reach Upstox during login. Check your internet connection and try again.";
    } else if (reason === "restart") {
      text = "Login succeeded but the market feed could not restart. Try toggling Login again, or restart MarketHub.";
    } else if (reason === "error") {
      text = "No Upstox feed is configured in MarketHub. Check that config.json contains an enabled 'upstox_feed' source, then restart MarketHub.";
    } else {
      text = "Upstox authentication failed. Please try again.";
    }
    msg.textContent = text;
    msg.className = "hint err";
  }
  // Strip auth parameters from browser history (no code/state retained).
  params.delete("auth");
  params.delete("reason");
  params.delete("fyers_auth");
  const qs = params.toString();
  history.replaceState(null, "", window.location.pathname +
    (qs ? "?" + qs : "") + location.hash);
}

export function initAuth() {
  const btn = $("auth-submit");
  const input = $("auth-token-input");
  const msg = $("auth-message");
  const loginBtn = $("oauth-login-btn");
  if (loginBtn) {
    loginBtn.addEventListener("click", () => {
      window.location.href = "/api/auth/upstox/login";
    });
  }
  const pinLoginBtn = $("oauth-login-pin-btn");
  if (pinLoginBtn) {
    pinLoginBtn.addEventListener("click", () => {
      window.location.href = "/api/auth/upstox/login?pin=1";
    });
  }
  const pinBtn = $("upstox-pin-btn");
  const pinInput = $("upstox-pin");
  if (pinBtn && pinInput) {
    pinBtn.addEventListener("click", async () => {
      const pin = pinInput.value.trim();
      msg.textContent = "";
      msg.className = "hint";
      if (!pin) {
        msg.textContent = "Please enter your Upstox PIN.";
        msg.className = "hint err";
        return;
      }
      pinBtn.disabled = true;
      pinBtn.textContent = "Logging in…";
      try {
        const res = await fetch("/api/auth/upstox/pin", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pin }),
        });
        const data = await res.json();
        if (res.ok) {
          pinInput.value = "";           // clear immediately — never retain
          msg.textContent = "Upstox login successful. Connecting market feed…";
          msg.className = "hint ok";
          pollAuthStatus();
          if (typeof pollSources === "function") pollSources();
        } else {
          msg.textContent = data.error ||
            "PIN login failed. Check your PIN and try again.";
          msg.className = "hint err";
        }
      } catch {
        msg.textContent = "Network error during PIN login.";
        msg.className = "hint err";
      } finally {
        pinBtn.disabled = false;
        pinBtn.textContent = "Login with PIN";
      }
    });
    pinInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") pinBtn.click();
    });
  }
  btn.addEventListener("click", async () => {
    const token = input.value.trim();
    msg.textContent = "";
    msg.className = "hint";
    if (!token) {
      msg.textContent = "Please paste an access token first.";
      msg.classList.add("err");
      return;
    }
    btn.disabled = true;
    btn.textContent = "Saving…";
    try {
      const res = await fetch("/api/auth/upstox/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ access_token: token }),
      });
      const data = await res.json();
      if (res.ok && data.configured) {
        input.value = "";           // clear immediately — never retain
        msg.textContent = "Token saved for this session.";
        msg.classList.add("ok");
        pollAuthStatus();
        pollSources();
      } else {
        msg.textContent = data.error || "Authentication failed. Access token may be invalid or expired.";
        msg.classList.add("err");
      }
    } catch {
      msg.textContent = "Network error while submitting token.";
      msg.classList.add("err");
    } finally {
      btn.disabled = false;
      btn.textContent = "Save Token";
    }
  });
  // Enter key submits too.
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") btn.click();
  });

  // Upstox feed enable/disable (durable config + runtime apply).
  const feedToggle = $("upstox-feed-toggle");
  const feedMsg = $("upstox-feed-msg");
  if (feedToggle) {
    feedToggle.addEventListener("click", async () => {
      feedMsg.textContent = "";
      feedMsg.className = "hint";
      const enabling = !feedToggle.textContent.includes("Disable");
      feedToggle.disabled = true;
      try {
        const res = await fetch("/api/settings/upstox/feed", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: enabling }),
        });
        const d = await res.json();
        if (res.ok) {
          if (d.restart_required) {
            feedMsg.textContent = enabling
              ? "Upstox feed enabled — restart MarketHub to activate it."
              : "Upstox feed disabled — restart MarketHub to fully stop it.";
            feedMsg.className = "hint";
          } else {
            feedMsg.textContent = enabling
              ? "Upstox feed enabled." : "Upstox feed disabled.";
            feedMsg.className = "hint ok";
          }
          pollUpstoxFeed();
        } else {
          feedMsg.textContent = d.error || "Failed to update feed configuration.";
          feedMsg.className = "hint err";
        }
      } catch {
        feedMsg.textContent = "Network error updating feed.";
        feedMsg.className = "hint err";
      } finally {
        feedToggle.disabled = false;
      }
    });
  }

  // Forget the durably stored Upstox session (keeps API credentials).
  const forgetUpstox = $("upstox-forget-session");
  const upxMsg = $("upstox-session-msg");
  if (forgetUpstox) {
    forgetUpstox.addEventListener("click", async () => {
      if (!confirm("Forget the saved Upstox session? You will need to log in again after the next restart.")) return;
      forgetUpstox.disabled = true;
      try {
        const res = await fetch("/api/auth/upstox/session", { method: "DELETE" });
        if (res.ok) {
          upxMsg.textContent = "Saved Upstox session forgotten.";
          upxMsg.className = "hint ok";
          pollAuthStatus();
        } else {
          upxMsg.textContent = "Failed to forget session.";
          upxMsg.className = "hint err";
        }
      } catch {
        upxMsg.textContent = "Network error.";
        upxMsg.className = "hint err";
      } finally { forgetUpstox.disabled = false; }
    });
  }
  // Reconnect the Upstox feed through the source manager (no full restart).
  const reconnectUpstox = $("upstox-reconnect");
  if (reconnectUpstox) {
    reconnectUpstox.addEventListener("click", async () => {
      reconnectUpstox.disabled = true;
      try {
        const res = await fetch("/api/sources/upstox/restart", { method: "POST" });
        const data = await res.json();
        upxMsg.textContent = data.ok
          ? "Feed reconnect triggered."
          : (data.error || "Reconnect failed.");
        upxMsg.className = "hint " + (data.ok ? "ok" : "err");
        if (typeof pollSources === "function") pollSources();
      } catch {
        upxMsg.textContent = "Network error.";
        upxMsg.className = "hint err";
      } finally { reconnectUpstox.disabled = false; }
    });
  }

  handleAuthCallbackParam();
  pollAuthStatus();
}

// ── Upstox app credentials (Settings page) ──────────────────────────────

async function pollCredStatus() {
  try {
    const res = await fetch("/api/settings/upstox");
    const d = await res.json();
    const keyChip = $("cred-key-status");
    const secretChip = $("cred-secret-status");
    if (d.store_error) {
      // Ciphertext exists but the current master.key cannot read it.
      if (keyChip) {
        keyChip.textContent = "Store Error";
        keyChip.className = "chip chip-off";
      }
      if (secretChip) {
        secretChip.textContent = "Store Error";
        secretChip.className = "chip chip-off";
      }
      const credMsg = $("cred-message");
      if (credMsg) {
        credMsg.textContent =
          "Encrypted credentials exist but the current master.key cannot " +
          "read them. Restore the matching master.key backup — do NOT " +
          "re-save credentials over them unless you intend to replace.";
        credMsg.className = "hint err";
      }
      return;
    }
    if (keyChip) {
      keyChip.textContent = d.api_key_configured
        ? "API Key: Configured" : "API Key: Missing";
      keyChip.className = "chip " + (d.api_key_configured ? "chip-on" : "chip-off");
    }
    if (secretChip) {
      secretChip.textContent = d.api_secret_configured
        ? "API Secret: Configured" : "API Secret: Missing";
      secretChip.className = "chip " + (d.api_secret_configured ? "chip-on" : "chip-off");
    }
    // Sources page summary chip.
    const srcCred = $("auth-cred-status");
    if (srcCred) {
      const ok = d.api_key_configured && d.api_secret_configured;
      srcCred.textContent = ok ? "Configured" : "Missing";
      srcCred.className = "chip " + (ok ? "chip-on" : "chip-off");
    }
  } catch { /* silent */ }
}

export function initCredentialSettings() {
  const saveBtn = $("cred-save");
  if (!saveBtn) return;
  const keyInput = $("cred-api-key");
  const secretInput = $("cred-api-secret");
  const msg = $("cred-message");
  saveBtn.addEventListener("click", async () => {
    msg.textContent = "";
    msg.className = "hint";
    const apiKey = keyInput.value.trim();
    const apiSecret = secretInput.value.trim();
    if (!apiKey || !apiSecret) {
      msg.textContent = "Both API key and API secret are required.";
      msg.classList.add("err");
      return;
    }
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    try {
      const res = await fetch("/api/settings/upstox", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: apiKey, api_secret: apiSecret }),
      });
      const data = await res.json();
      if (res.ok && data.configured) {
        keyInput.value = "";
        secretInput.value = "";   // never retain the secret in the field
        msg.textContent = "Credentials saved. You can now use Login with Upstox on the Sources page.";
        msg.classList.add("ok");
        pollCredStatus();
        pollAuthStatus();
      } else {
        msg.textContent = data.error || "Failed to save credentials.";
        msg.classList.add("err");
      }
    } catch {
      msg.textContent = "Network error while saving credentials.";
      msg.classList.add("err");
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = "Save Credentials";
    }
  });
  pollCredStatus();
}

export function initCredentialDelete() {
  const delBtn = $("cred-delete");
  if (!delBtn) return;
  const msg = $("cred-message");
  delBtn.addEventListener("click", async () => {
    if (!confirm("Delete stored Upstox API credentials?")) return;
    delBtn.disabled = true;
    try {
      const res = await fetch("/api/settings/upstox", { method: "DELETE" });
      if (res.ok) {
        $("cred-api-key").value = "";
        $("cred-api-secret").value = "";
        msg.textContent = "Credentials deleted.";
        msg.className = "hint ok";
        pollCredStatus();
        pollAuthStatus();
      } else {
        msg.textContent = "Failed to delete credentials.";
        msg.className = "hint err";
      }
    } catch {
      msg.textContent = "Network error while deleting credentials.";
      msg.className = "hint err";
    } finally {
      delBtn.disabled = false;
    }
  });
}

// ── Fyers credentials + login (Settings → Brokers) ────────────────────────

export function initFyers() {
  const saveBtn = $("fyers-save");
  if (!saveBtn) return;
  const msg = $("fyers-message");
  const loginBtn = $("fyers-login-btn");

  async function refresh() {
    try {
      const res = await fetch("/api/settings/fyers");
      const d = await res.json();
      // App Credentials: are App ID + Secret saved (encrypted)?
      const credChip = $("fyers-status");
      if (d.store_error) {
        credChip.textContent = "Credential Store Error";
        credChip.className = "chip chip-off";
        msg.textContent =
          "Encrypted credentials exist but the current master.key cannot " +
          "read them. Restore the matching master.key backup — do NOT " +
          "re-save credentials over them unless you intend to replace.";
        msg.className = "hint err";
      } else if (d.app_id_configured && d.secret_configured) {
        credChip.textContent = "Configured";
        credChip.className = "chip chip-on";
      } else {
        credChip.textContent = "Not configured";
        credChip.className = "chip chip-off";
      }
      // Daily Login: distinct from credentials — is a session usable now?
      const loginChip = $("fyers-login-status");
      if (!d.login_available) {
        loginChip.textContent = "Credentials Required";
        loginChip.className = "chip chip-off";
      } else if (d.access_token_active) {
        loginChip.textContent = "Daily Login Active";
        loginChip.className = "chip chip-on";
      } else {
        loginChip.textContent = "Login Required";
        loginChip.className = "chip chip-off";
      }
      loginBtn.classList.toggle("hidden", !d.login_available);
      // Feed runtime state comes from the source manager, not auth.
      let src = null;
      try {
        const sres = await fetch("/api/sources/status");
        const sd = await sres.json();
        src = (sd.sources || []).find(
          (s) => s.name === "fyers" || (s.type || "").indexOf("fyers") >= 0);
      } catch { /* keep placeholder */ }
      const fySrcState = d.source_state || (src && src.state) || "unknown";
      $("fyers-feed-state").textContent =
        d.source_registered ? friendlyState(fySrcState) : "source not configured";
      // Feed enable/disable toggle reflects the durable source config.
      const fyToggle = $("fyers-feed-toggle");
      const fyChip = $("fyers-feed-chip");
      if (fyToggle) {
        const en = !!d.source_enabled;
        fyToggle.textContent = en ? "Disable Feed" : "Enable Feed";
        fyToggle.classList.toggle("btn-outline-danger", en);
      }
      if (fyChip) {
        const en = !!d.source_enabled;
        fyChip.textContent = en ? "Enabled" : "Disabled";
        fyChip.className = "chip " + (en ? "chip-on" : "chip-off");
      }

      // ── Broker state grid: eight independent real states ──
      const fyCreds = d.app_id_configured && d.secret_configured;
      setChip("fy-configured",
        fyCreds ? "Configured" : "Not Configured",
        fyCreds ? "is-on" : "is-off");
      setChip("fy-creds",
        fyCreds ? "Credentials Saved" : "No Credentials",
        fyCreds ? "is-on" : "is-off");
      setChip("fy-auth",
        d.access_token_active ? "Authenticated" : "Not Authenticated",
        d.access_token_active ? "is-on" : "is-off");
      setChip("fy-restored",
        d.session_restored ? "Session Restored" : "No Session Restore",
        d.session_restored ? "is-on" : "is-off");
      const fyRunning = d.source_registered && fySrcState
        && !["stopped", "auth_required"].includes(fySrcState);
      setChip("fy-feed",
        fyRunning ? "Feed Active" : "Feed Inactive",
        fyRunning ? "is-on" : "is-off");
      if (d.access_token_expires_at) {
        setChip("fy-expiry",
          "Expiry " + new Date(d.access_token_expires_at).toLocaleString(),
          "is-warn");
      } else {
        setChip("fy-expiry", "Expiry —", "");
      }
      setChip("fy-recovery",
        d.restart_recovery ? "Restart Recovery On" : "Restart Recovery Off",
        d.restart_recovery ? "is-on" : "is-off");
      setChip("fy-loginreq",
        d.login_required ? "Login Required" : "No Login Needed",
        d.login_required ? "is-off" : "is-on");
    } catch { /* silent */ }
  }

  saveBtn.addEventListener("click", async () => {
    const appId = $("fyers-app-id").value.trim();
    const secret = $("fyers-secret").value.trim();
    msg.textContent = "";
    msg.className = "hint";
    if (!appId || !secret) {
      msg.textContent = "Both App ID and Secret Key are required.";
      msg.className = "hint err";
      return;
    }
    saveBtn.disabled = true;
    try {
      const pin = ($("fyers-pin") || {}).value?.trim() || "";
      const res = await fetch("/api/settings/fyers", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ app_id: appId, secret_id: secret,
                               pin: pin }) });
      const d = await res.json();
      if (res.ok && d.configured) {
        $("fyers-app-id").value = "";
        $("fyers-secret").value = "";
        msg.textContent = "Fyers credentials saved.";
        msg.className = "hint ok";
        refresh();
      } else {
        msg.textContent = d.error || "Failed to save Fyers credentials.";
        msg.className = "hint err";
      }
    } catch {
      msg.textContent = "Network error saving Fyers credentials.";
      msg.className = "hint err";
    } finally { saveBtn.disabled = false; }
  });

  loginBtn.addEventListener("click", () => {
    window.location.href = "/api/auth/fyers/login";
  });

  // Forget the saved Fyers session (keeps App ID/Secret credentials).
  const forgetFyers = $("fyers-forget-session");
  const fyMsg = $("fyers-session-msg");
  if (forgetFyers) {
    forgetFyers.addEventListener("click", async () => {
      if (!confirm("Forget the saved Fyers session? You will need to log in again after the next restart.")) return;
      forgetFyers.disabled = true;
      try {
        const res = await fetch("/api/auth/fyers/session", { method: "DELETE" });
        if (res.ok) {
          fyMsg.textContent = "Saved Fyers session forgotten.";
          fyMsg.className = "hint ok";
          refresh();
        } else {
          fyMsg.textContent = "Failed to forget session.";
          fyMsg.className = "hint err";
        }
      } catch {
        fyMsg.textContent = "Network error.";
        fyMsg.className = "hint err";
      } finally { forgetFyers.disabled = false; }
    });
  }
  // Reconnect the Fyers feed through the source manager (no full restart).
  const reconnectFyers = $("fyers-reconnect");
  if (reconnectFyers) {
    reconnectFyers.addEventListener("click", async () => {
      reconnectFyers.disabled = true;
      try {
        const res = await fetch("/api/sources/fyers/restart", { method: "POST" });
        const data = await res.json();
        fyMsg.textContent = data.ok
          ? "Feed reconnect triggered."
          : (data.error || "Reconnect failed.");
        fyMsg.className = "hint " + (data.ok ? "ok" : "err");
        refresh();
        if (typeof pollSources === "function") pollSources();
      } catch {
        fyMsg.textContent = "Network error.";
        fyMsg.className = "hint err";
      } finally { reconnectFyers.disabled = false; }
    });
  }

  // Fyers feed enable/disable (durable config + runtime apply, no restart).
  const fyFeedToggle = $("fyers-feed-toggle");
  const fyFeedMsg = $("fyers-feed-msg");
  if (fyFeedToggle) {
    fyFeedToggle.addEventListener("click", async () => {
      fyFeedMsg.textContent = "";
      fyFeedMsg.className = "hint";
      const enabling = !fyFeedToggle.textContent.includes("Disable");
      fyFeedToggle.disabled = true;
      try {
        const res = await fetch("/api/settings/fyers/feed", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: enabling }),
        });
        const d2 = await res.json();
        if (res.ok) {
          fyFeedMsg.textContent = enabling
            ? "Fyers feed enabled." : "Fyers feed disabled.";
          fyFeedMsg.className = "hint ok";
          refresh();
          if (typeof pollSources === "function") pollSources();
        } else {
          fyFeedMsg.textContent = d2.error || "Failed to update Fyers feed.";
          fyFeedMsg.className = "hint err";
        }
      } catch {
        fyFeedMsg.textContent = "Network error updating Fyers feed.";
        fyFeedMsg.className = "hint err";
      } finally {
        fyFeedToggle.disabled = false;
      }
    });
  }

  refresh();
}
