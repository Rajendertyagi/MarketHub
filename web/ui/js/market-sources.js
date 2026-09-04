/**
 * MarketHub WebUI — broker/source lifecycle UI (market data sources).
 *
 * NOTE: this is the market-data Sources surface, separate from the News
 * source CRUD in sources.js.
 *
 * Owns source-status polling state, lifecycle controls, and rendering.
 * Reads the active view (router) and daily-auth snapshot (auth) through
 * explicit module imports — no window globals.
 */

import { $, escDash } from "./utils.js";
import { currentView } from "./router.js";
import { renderMarketStatus, renderMovers } from "./market.js";
import { getAuthStatus } from "./auth.js";

let lastSourcesSnapshot = [];            // latest /api/sources/status payload
const sourceActionInFlight = new Map();  // source name → in-flight action

export function getSourcesSnapshot() {
  return lastSourcesSnapshot;
}

// ── Source status polling ───────────────────────────────────────────────

export async function pollSources() {
  try {
    const res = await fetch("/api/sources/status");
    const data = await res.json();
    const sources = data.sources || [];
    lastSourcesSnapshot = sources;

    // Topbar: the dedicated upstox chip tracks the upstox source (if any);
    // the generic broker indicator aggregates ALL sources.
    const upstox = sources.find((s) => s.name === "upstox") || sources[0];
    if (upstox) {
      const st = upstox.state || "unknown";
      $("chip-upstox").textContent = friendlyShort(st);
      $("chip-upstox").className = "chip " +
        (st === "streaming" ? "chip-on" : st === "failed" ? "chip-off" : "");
      $("chip-instruments").textContent = upstox.configured_instruments ?? 0;
      $("chip-reconnects").textContent = upstox.reconnect_count ?? 0;
    }
    // Aggregate across all sources (not just the first match above).
    $("chip-instruments").textContent =
      sources.reduce((a, s) => a + (s.configured_instruments || 0), 0);
    $("chip-frames").textContent =
      sources.reduce((a, s) => a + (s.frames_received || 0), 0);
    const labels = sources.map((s) => `${s.name}: ${friendlyShort(s.state || "unknown")}`);
    const anyStreaming = sources.some((s) => s.state === "streaming");
    $("broker-indicator").textContent = "● " + (labels.join("  |  ") || "no sources");
    $("broker-indicator").className = "indicator " +
      (anyStreaming ? "indicator-on" : "indicator-off");

    renderMovers();
    renderMarketStatus();
    // Update per-broker source detail tables inside Settings (single page).
    if (currentView === "settings") {
      renderAllSourceDetails(sources);
    }
  } catch { /* silent */ }
}

function friendlyState(state) {
  const map = {
    auth_required: "Stopped — daily login required",
    failed: "Failed",
    stopped: "Stopped",
    streaming: "Streaming",
    connecting: "Connecting",
    authorizing: "Authorizing",
    reconnecting: "Reconnecting",
  };
  return map[state] || state || "—";
}

// Compact topbar label (WP23): no long explanations in the header chip.
function friendlyShort(state) {
  const map = {
    auth_required: "Login Required",
    streaming: "Streaming",
    connecting: "Connecting",
    authorizing: "Authorizing",
    reconnecting: "Reconnecting",
    failed: "Failed",
    stopped: "Stopped",
  };
  return map[state] || state || "—";
}

// Human-readable stop reason (WP4/15).
function stopReasonLabel(reason) {
  if (!reason) return "—";
  if (reason.startsWith("terminal:")) return "Terminal failure (non-retryable)";
  if (reason.startsWith("error:")) return "Internal error";
  const map = {
    operator_stop: "Operator stopped (Stop Feed)",
    application_shutdown: "Application shutdown",
    restart: "Restarted",
    auth_required: "Daily login required",
    cancelled: "Cancelled",
    stop_requested: "Stop requested",
  };
  return map[reason] || reason;
}

// ── Source lifecycle controls (Sources page) ────────────────────────────

const ACTIVE_STATES = new Set(["streaming", "connecting", "authorizing", "reconnecting"]);

function usableCreds(s) {
  // Upstox has a daily-auth gate; the backend's ready_to_start flag is the
  // ground truth (placeholders/known-expired tokens report false). Sources
  // without a declared gate are treated as usable.
  if ((s.provider || "") !== "upstox") return true;
  const a = getAuthStatus();
  return !!(a && a.ready_to_start === true);
}

function sourceButtonsHtml(s) {
  const name = escDash(s.name);
  const state = s.state || "stopped";
  const inflight = sourceActionInFlight.get(s.name);
  const ready = usableCreds(s);
  const busy = (action, label) =>
    `<button class="btn" data-src-action="${action}" data-src-name="${name}"` +
    `${inflight ? " disabled" : ""}` +
    `${inflight === action ? ` style="opacity:.6">${label}…</button>` : `>${label}</button>`}`;
  let html = "";
  // Daily-auth required (fresh boot with placeholder OR broker-rejected):
  // offer Login FIRST, keep Start disabled.
  if (!ready && (s.provider || "") === "upstox") {
    html += `<button class="btn" data-src-action="login" data-src-name="${name}"${inflight ? " disabled" : ""}>Login with Upstox</button> `;
  }
  if (ACTIVE_STATES.has(state)) {
    html += busy("restart", "Restart Feed") + " " + busy("stop", "Stop Feed");
    return html;
  }
  // stopped / failed / auth_required / unknown
  html += `<button class="btn" data-src-action="start" data-src-name="${name}"` +
    `${inflight || !ready ? " disabled" : ""}` +
    `${ready ? "" : ' title="Complete the daily login first."'}>Start Feed</button> `;
  html += busy("restart", "Restart Feed");
  return html;
}

function renderSourceDetail(provider, sources) {
  const el = document.getElementById(provider + "-src-detail");
  if (!el) return;
  const s = sources.find((x) => x.name === provider);
  if (!s) {
    el.innerHTML = "<em>Source not configured</em>";
    return;
  }
  const state = s.state || "—";
  const stateFriendly = friendlyState(state);
  const stateCls = state === "streaming" ? "chip chip-on"
    : state === "auth_required" ? "chip" : "chip chip-off";
  // Stale-state surfacing: an active label with a dead task means the
  // feed task exited without reporting (never hide this).
  const stale = s.task_running === false && ACTIVE_STATES.has(state);
  const staleHint = stale
    ? `<tr><td style="color:var(--text-muted)"></td>` +
      `<td style="color:var(--red)">Feed task exited — status may be stale` +
      `${s.last_exit_reason ? ` (${escDash(s.last_exit_reason)})` : ""}</td></tr>`
    : "";
  const authHint = state === "auth_required"
    ? '<tr><td style="color:var(--text-muted)"></td>' +
      '<td>Daily login required — use Login with Upstox below</td></tr>'
    : "";
  const creds = (s.provider || "") === "upstox"
    ? (usableCreds(s) ? "Configured" : "Missing / expired")
    : "—";
  const dailyLogin = (s.provider || "") === "upstox"
    ? (usableCreds(s) ? "Active" : "Required")
    : "—";
  const subd = s.subscribed_instruments != null
    ? `${s.configured_instruments ?? 0} desired / ${s.subscribed_instruments} subscribed`
    : `${s.configured_instruments ?? 0} desired`;
  el.innerHTML = `
    <table class="data-table">
      <tr><td style="width:180px;color:var(--text-muted)">Provider</td><td>${escDash(s.provider)}</td></tr>
      <tr><td style="color:var(--text-muted)">App Credentials</td><td>${creds}</td></tr>
      <tr><td style="color:var(--text-muted)">Daily Login</td><td>${dailyLogin}</td></tr>
      <tr><td style="color:var(--text-muted)">Feed State</td><td><span class="${stateCls}">${stateFriendly}</span></td></tr>
      <tr><td style="color:var(--text-muted)">Task Running</td><td>${s.task_running == null ? "—" : (s.task_running ? "Yes" : "No")}</td></tr>
      ${staleHint}
      ${authHint}
      <tr><td style="color:var(--text-muted)">Mode</td><td>${escDash(s.mode)}</td></tr>
      <tr><td style="color:var(--text-muted)">Instruments</td><td>${subd}</td></tr>
      <tr><td style="color:var(--text-muted)">Connect Attempts</td><td>${s.connect_attempts ?? 0}</td></tr>
      <tr><td style="color:var(--text-muted)">Reconnects</td><td>${s.reconnect_count ?? 0}${s.reconnecting ? " (reconnecting now)" : ""}</td></tr>
      <tr><td style="color:var(--text-muted)">Frames Received</td><td>${s.frames_received ?? 0}</td></tr>
      <tr><td style="color:var(--text-muted)">Malformed Frames</td><td>${s.malformed_frames ?? 0}</td></tr>
      <tr><td style="color:var(--text-muted)">Last Connected</td><td>${escDash(s.last_connected_at)}</td></tr>
      <tr><td style="color:var(--text-muted)">Last Message</td><td>${escDash(s.last_message_at)}</td></tr>
      <tr><td style="color:var(--text-muted)">Last Error</td><td>${escDash(s.last_error)}</td></tr>
      <tr><td style="color:var(--text-muted)">Last Exit</td><td>${escDash(s.last_exit_reason)}${s.last_exit_at ? ` at ${escDash(s.last_exit_at)}` : ""}</td></tr>
      <tr><td style="color:var(--text-muted)">Stop Reason</td><td>${stopReasonLabel(s.stop_reason)}</td></tr>
      ${s.not_ready_reason ? `<tr><td style="color:var(--text-muted)">Not Ready</td><td>${escDash(s.not_ready_reason)}</td></tr>` : ""}
      ${renderTransitions(s.recent_transitions)}
      <tr><td style="color:var(--text-muted)">Controls</td><td>${sourceButtonsHtml(s)}</td></tr>
    </table>`;
}

function renderAllSourceDetails(sources) {
  renderSourceDetail("upstox", sources);
  renderSourceDetail("fyers", sources);
}

function showSourcesMsg(text, ok, name) {
  const id = name ? name + "-src-msg" : "sources-msg";
  const msg = $(id);
  if (!msg) return;
  msg.textContent = text;
  msg.className = "hint " + (ok ? "ok" : "err");
}

// Compact, safe-only transition history (WP14). Reasons are backend-enumerated
// strings; never provider material.
function renderTransitions(transitions) {
  if (!Array.isArray(transitions) || !transitions.length) return "";
  const rows = transitions.slice(-8).reverse().map((t) => {
    const at = (t.at || "").replace("T", " ").slice(0, 19);
    const reason = t.reason ? ` (${escDash(t.reason)})` : "";
    return `<div style="font-family:var(--mono);font-size:11px;color:var(--text-muted)">` +
      `${escDash(at)} &nbsp; ${escDash(t.from)} → ${escDash(t.to)}${reason}</div>`;
  }).join("");
  return `<tr><td style="color:var(--text-muted);vertical-align:top">Recent Transitions</td>` +
    `<td>${rows}</td></tr>`;
}

async function sourceControlRequest(action, name) {
  if (action === "login") {
    window.location.href = "/api/auth/upstox/login";
    return;
  }
  sourceActionInFlight.set(name, action);
  try {
    renderAllSourceDetails(lastSourcesSnapshot || []);
    const res = await fetch(`/api/sources/${encodeURIComponent(name)}/${action}`,
                            { method: "POST" });
    let body = {};
    try { body = await res.json(); } catch { /* non-JSON */ }
    if (res.ok && body.ok) {
      if (action === "start") {
        showSourcesMsg(body.result === "already_running"
          ? "Feed already running — no duplicate started."
          : "Start requested — connecting…", true, name);
      } else if (action === "stop") {
        showSourcesMsg("Feed stopped. Credentials and subscriptions retained.", true, name);
      } else {
        showSourcesMsg("Restart requested — fresh authorize + reconnect…", true, name);
      }
    } else if (body.reason === "authentication_required") {
      showSourcesMsg("Daily login required.", false, name);
    } else if (body.reason === "unknown_source") {
      showSourcesMsg("Unknown source.", false, name);
    } else {
      showSourcesMsg(`Command failed (HTTP ${res.status}).`, false, name);
    }
  } catch {
    showSourcesMsg("Network error while controlling the feed.", false, name);
  } finally {
    sourceActionInFlight.delete(name);
    pollSources();   // immediate refresh of status + buttons
  }
}

let _controlsBound = false;

export function initSourceControls() {
  // Delegated on the Settings view: the per-broker detail tables render
  // Start/Stop/Restart (and Login) buttons with data-src-action / -name.
  // Bound once; the settings container persists across renders.
  if (_controlsBound) return;
  _controlsBound = true;
  const settings = $("view-settings");
  if (settings) {
    settings.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-src-action]");
      if (!btn || btn.disabled) return;
      sourceControlRequest(btn.dataset.srcAction, btn.dataset.srcName);
    });
  }
}
