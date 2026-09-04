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
import { pollSources } from "./market-sources.js";

let lastAuthStatus = null;   // /api/auth/upstox/status snapshot

/** Read-only daily-auth snapshot for market-sources controls. */
export function getAuthStatus() {
  return lastAuthStatus;
}

// ── Upstox auth (token submit) ──────────────────────────────────────────

export async function pollAuthStatus() {
  try {
    const res = await fetch("/api/auth/upstox/status");
    const d = await res.json();
    lastAuthStatus = d;   // consumed by the Sources page controls
    const chip = $("auth-token-status");
    const loginBtn = $("oauth-login-btn");
    if (loginBtn) {
      // Only offer login when BOTH credentials and a live feed exist.
      const ready = d.oauth_available && d.configured !== false;
      loginBtn.style.display = ready ? "" : "none";
      if (ready) {
        loginBtn.disabled = d.auth_state === "authorizing";
        loginBtn.textContent = d.token_configured
          ? "Login with Upstox (renew)" : "Login with Upstox";
      }
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
