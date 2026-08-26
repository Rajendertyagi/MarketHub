/**
 * MarketHub — compact trading terminal application logic.
 *
 * One EventSource connection to /api/market/stream.
 * In-place DOM updates (no full rebuild).
 * Client-side section switching (no backend routes per view).
 */
"use strict";

(() => {
  // ── DOM shortcuts ───────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);

  // ── State ───────────────────────────────────────────────────────────────
  const quotes = new Map();       // composite_key → quote data object
  let es = null;                  // EventSource instance
  let currentView = "dashboard";
  const dashRows = new Map();     // key → <tr> element (in-place update)
  const mktRows = new Map();
  let lastAuthStatus = null;               // /api/auth/upstox/status snapshot
  const sourceActionInFlight = new Map();  // source name → in-flight action
  let lastSourcesSnapshot = [];            // latest /api/sources/status payload
  let alertPushSource = null;              // /events/stream (alert push)

  // ── Utilities ───────────────────────────────────────────────────────────

  const fmt = (v, dp = 2) =>
    v != null ? Number(v).toLocaleString("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp }) : "—";

  const fmtVol = (v) => {
    if (v == null) return "—";
    if (v >= 1e7) return (v / 1e7).toFixed(2) + "Cr";
    if (v >= 1e5) return (v / 1e5).toFixed(2) + "L";
    if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
    return String(v);
  };

  const chgClass = (v) => v > 0 ? "up" : v < 0 ? "down" : "";

  const nowStr = () => new Date().toLocaleTimeString();

  function setIndicator(id, on, text) {
    const el = $(id);
    el.className = "indicator " + (on ? "indicator-on" : "indicator-off");
    el.textContent = text;
  }

  // ── Theme ───────────────────────────────────────────────────────────────

  function initTheme() {
    const saved = localStorage.getItem("mh-theme") || "dark";
    applyTheme(saved);
    $("theme-toggle").addEventListener("click", toggleTheme);
    $("settings-theme-toggle").addEventListener("click", toggleTheme);
  }

  function applyTheme(theme) {
    document.documentElement.className = theme === "light" ? "wa-theme-light" : "wa-theme-dark";
    localStorage.setItem("mh-theme", theme);
  }

  function toggleTheme() {
    const cur = document.documentElement.classList.contains("wa-theme-light") ? "dark" : "light";
    applyTheme(cur);
  }

  // ── Navigation ──────────────────────────────────────────────────────────

  function initNav() {
    document.querySelectorAll(".nav-link").forEach((btn) => {
      btn.addEventListener("click", () => switchView(btn.dataset.view));
    });
    const hashView = location.hash.startsWith("#/")
      ? location.hash.slice(2) : null;
    let saved = null;
    try { saved = sessionStorage.getItem("mh-last-view"); } catch {}
    const initial = document.getElementById("view-" + hashView)
      ? hashView
      : (document.getElementById("view-" + saved) ? saved : "dashboard");
    switchView(initial);
  }

  function switchView(view) {
    currentView = view;
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    const el = $("view-" + view);
    if (el) el.classList.add("active");
    document.querySelectorAll(".nav-link").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === view);
    });
    // Source status is only re-rendered while on the Sources view. Trigger an
    // immediate poll so the panel isn't stuck on its initial "Loading…" state.
    if (view === "sources") pollSources();
    try {
      sessionStorage.setItem("mh-last-view", view);
      if (location.hash !== "#/" + view) {
        history.replaceState(null, "", location.pathname + "#/" + view);
      }
    } catch { /* storage unavailable */ }
  }

  // ── SSE connection ──────────────────────────────────────────────────────

  function connectSSE() {
    es = new EventSource("/api/market/stream");

    es.onopen = () => {
      setIndicator("sse-indicator", true, "● SSE");
      $("chip-sse").textContent = "Connected";
      $("chip-sse").className = "chip chip-on";
    };

    es.onerror = () => {
      setIndicator("sse-indicator", false, "● SSE");
      $("chip-sse").textContent = "Reconnecting";
      $("chip-sse").className = "chip chip-off";
    };

    es.addEventListener("quote", (e) => {
      try {
        const envelope = JSON.parse(e.data);
        if (envelope.type === "quote" && envelope.data) {
          handleQuoteUpdate(envelope.data);
        }
      } catch { /* malformed frame — skip */ }
    });
  }

  function handleQuoteUpdate(data) {
    const key = data.exchange + ":" + data.instrument_token;
    quotes.set(key, data);

    // Update dashboard row in place.
    updateDashRow(key, data);
    // Update markets row in place.
    updateMktRow(key, data);
    // Update ticker strip.
    updateTicker(key, data);
    // Update market cards (dashboard top).
    updateCards(key, data);

    // Stale-data indicator: quote timestamps older than 5 minutes are
    // marked explicitly so old prices never read as live ticks.
    const lastMs = Date.parse(data.received_ts || "") || 0;
    const ageMin = lastMs ? (Date.now() - lastMs) / 60000 : 999;
    $("chip-last-update").textContent =
      (ageMin > 5 ? "[STALE] " : "") + nowStr();
    $("footer-tick").textContent =
      `${quotes.size} instruments · ${nowStr()}`;
  }

  // ── Dashboard table (in-place updates) ─────────────────────────────────

  function updateDashRow(key, q) {
    let row = dashRows.get(key);
    if (!row) {
      row = document.createElement("tr");
      row.dataset.key = key;
      row.innerHTML = `
        <td class="sym"></td><td class="ltp"></td><td class="chg"></td>
        <td class="pct"></td><td class="open"></td><td class="high"></td>
        <td class="low"></td><td class="close"></td><td class="vol"></td>
        <td class="bid"></td><td class="ask"></td><td class="upd"></td>`;
      const body = $("dash-body");
      if (body.querySelector(".empty-row")) body.innerHTML = "";
      body.appendChild(row);
      dashRows.set(key, row);
    }
    const chg = q.change ?? 0;
    const cls = chgClass(chg);
    row.querySelector(".sym").textContent = q.tradingsymbol || key;
    row.querySelector(".ltp").textContent = fmt(q.ltp);
    const chgEl = row.querySelector(".chg");
    chgEl.textContent = fmt(chg);
    chgEl.className = "chg " + cls;
    const pctEl = row.querySelector(".pct");
    pctEl.textContent = q.change_percent != null ? fmt(q.change_percent) + "%" : "—";
    pctEl.className = "pct " + cls;
    row.querySelector(".open").textContent = fmt(q.open);
    row.querySelector(".high").textContent = fmt(q.high);
    row.querySelector(".low").textContent = fmt(q.low);
    row.querySelector(".close").textContent = fmt(q.close);
    row.querySelector(".vol").textContent = fmtVol(q.volume);
    row.querySelector(".bid").textContent = fmt(q.best_bid);
    row.querySelector(".ask").textContent = fmt(q.best_ask);
    row.querySelector(".upd").textContent =
      q.received_ts ? new Date(q.received_ts).toLocaleTimeString() : "—";
  }

  // ── Markets table (full-width, more columns) ───────────────────────────

  function updateMktRow(key, q) {
    let row = mktRows.get(key);
    if (!row) {
      row = document.createElement("tr");
      row.dataset.key = key;
      row.style.cursor = "pointer";
      row.innerHTML = `
        <td class="sym"></td><td class="ltp"></td><td class="chg"></td>
        <td class="pct"></td><td class="open"></td><td class="high"></td>
        <td class="low"></td><td class="close"></td><td class="atp"></td>
        <td class="vol"></td><td class="oi"></td><td class="oichg"></td>
        <td class="oipct"></td><td class="bid"></td><td class="ask"></td>
        <td class="uckt"></td><td class="lckt"></td>
        <td class="ltt"></td><td class="upd"></td>`;
      const body = $("markets-body");
      if (body.querySelector(".empty-row")) body.innerHTML = "";
      body.appendChild(row);
      mktRows.set(key, row);
    }
    const chg = q.change ?? 0;
    const cls = chgClass(chg);
    const set = (sel, text, extraCls) => {
      const el = row.querySelector(sel);
      if (!el) return;
      el.textContent = text;
      if (extraCls !== undefined) el.className = sel.slice(1) + (extraCls ? " " + extraCls : "");
    };
    set(".sym", q.tradingsymbol || key);
    set(".ltp", fmt(q.ltp));
    set(".chg", fmt(chg), cls);
    set(".pct", q.change_percent != null ? fmt(q.change_percent) + "%" : "—", cls);
    set(".open", q.open != null ? fmt(q.open) : "—");
    set(".high", q.high != null ? fmt(q.high) : "—");
    set(".low", q.low != null ? fmt(q.low) : "—");
    set(".close", q.close != null ? fmt(q.close) : "—");
    set(".atp", q.avg_trade_price != null ? fmt(q.avg_trade_price) : "—");
    set(".vol", fmtVol(q.volume));
    set(".oi", fmtVol(q.open_interest));
    set(".oichg", fmtNum(q.oi_change));
    set(".oipct", q.oi_change_percent != null ? fmt(q.oi_change_percent) + "%" : "—");
    set(".bid", q.best_bid != null ? fmt(q.best_bid) : "—");
    set(".ask", q.best_ask != null ? fmt(q.best_ask) : "—");
    set(".uckt", q.upper_circuit != null ? fmt(q.upper_circuit) : "—");
    set(".lckt", q.lower_circuit != null ? fmt(q.lower_circuit) : "—");
    set(".ltt", fmtTs(q.last_trade_time));
    set(".upd", q.received_ts ? new Date(q.received_ts).toLocaleTimeString() : "—");
  }

  // ── Ticker strip ────────────────────────────────────────────────────────

  function updateTicker(key, q) {
    const strip = $("ticker-strip");
    if (strip.querySelector(".ticker-empty")) strip.innerHTML = "";
    let item = strip.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (!item) {
      item = document.createElement("span");
      item.className = "ticker-item";
      item.dataset.key = key;
      item.innerHTML = `<span class="ticker-sym"></span><span class="ticker-ltp"></span><span class="ticker-chg"></span>`;
      strip.appendChild(item);
    }
    const chg = q.change ?? 0;
    item.querySelector(".ticker-sym").textContent = q.tradingsymbol || key.split("|").pop();
    item.querySelector(".ticker-ltp").textContent = fmt(q.ltp);
    const chgEl = item.querySelector(".ticker-chg");
    chgEl.textContent = `${chg > 0 ? "+" : ""}${fmt(chg)} (${q.change_percent != null ? fmt(q.change_percent) + "%" : ""})`;
    chgEl.className = "ticker-chg " + chgClass(chg);
  }

  // ── Market cards (dashboard top) ────────────────────────────────────────

  function updateCards(key, q) {
    const container = $("market-cards");
    let card = container.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (!card) {
      card = document.createElement("div");
      card.className = "mcard";
      card.dataset.key = key;
      card.innerHTML = `<div class="mcard-sym"></div><div class="mcard-ltp"></div><div class="mcard-chg"></div>`;
      container.appendChild(card);
    }
    const chg = q.change ?? 0;
    card.querySelector(".mcard-sym").textContent = q.tradingsymbol || key.split("|").pop();
    card.querySelector(".mcard-ltp").textContent = fmt(q.ltp);
    const chgEl = card.querySelector(".mcard-chg");
    chgEl.textContent = `${chg > 0 ? "+" : ""}${fmt(chg)} (${q.change_percent != null ? fmt(q.change_percent) + "%" : ""})`;
    chgEl.className = "mcard-chg " + chgClass(chg);
  }

  // ── Source status polling ───────────────────────────────────────────────

  async function pollSources() {
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
      // Update sources detail panel (only when visible).
      if (currentView === "sources") {
        renderSourcesDetail(sources);
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

  const esc = (v) => String(v ?? "—")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

  const ACTIVE_STATES = new Set(["streaming", "connecting", "authorizing", "reconnecting"]);

  function usableCreds(s) {
    // Upstox has a daily-auth gate; the backend's ready_to_start flag is the
    // ground truth (placeholders/known-expired tokens report false). Sources
    // without a declared gate are treated as usable.
    if ((s.provider || "") !== "upstox") return true;
    const a = lastAuthStatus;
    return !!(a && a.ready_to_start === true);
  }

  function sourceButtonsHtml(s) {
    const name = esc(s.name);
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

  function renderSourcesDetail(sources) {
    const el = $("sources-detail");
    if (!sources.length) {
      el.innerHTML = "<em>No sources configured</em>";
      return;
    }
    el.innerHTML = sources.map((s) => {
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
          `${s.last_exit_reason ? ` (${esc(s.last_exit_reason)})` : ""}</td></tr>`
        : "";
      const authHint = state === "auth_required"
        ? '<tr><td style="color:var(--text-muted)"></td>' +
          '<td>Daily login required — use Login with Upstox below or in Settings</td></tr>'
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
      return `
      <table class="data-table">
        <tr><td style="width:180px;color:var(--text-muted)">Name</td><td><strong>${esc(s.name)}</strong></td></tr>
        <tr><td style="color:var(--text-muted)">Provider</td><td>${esc(s.provider)}</td></tr>
        <tr><td style="color:var(--text-muted)">App Credentials</td><td>${creds}</td></tr>
        <tr><td style="color:var(--text-muted)">Daily Login</td><td>${dailyLogin}</td></tr>
        <tr><td style="color:var(--text-muted)">Feed State</td><td><span class="${stateCls}">${stateFriendly}</span></td></tr>
        <tr><td style="color:var(--text-muted)">Task Running</td><td>${s.task_running == null ? "—" : (s.task_running ? "Yes" : "No")}</td></tr>
        ${staleHint}
        ${authHint}
        <tr><td style="color:var(--text-muted)">Mode</td><td>${esc(s.mode)}</td></tr>
        <tr><td style="color:var(--text-muted)">Instruments</td><td>${subd}</td></tr>
        <tr><td style="color:var(--text-muted)">Connect Attempts</td><td>${s.connect_attempts ?? 0}</td></tr>
        <tr><td style="color:var(--text-muted)">Reconnects</td><td>${s.reconnect_count ?? 0}${s.reconnecting ? " (reconnecting now)" : ""}</td></tr>
        <tr><td style="color:var(--text-muted)">Frames Received</td><td>${s.frames_received ?? 0}</td></tr>
        <tr><td style="color:var(--text-muted)">Malformed Frames</td><td>${s.malformed_frames ?? 0}</td></tr>
        <tr><td style="color:var(--text-muted)">Last Connected</td><td>${esc(s.last_connected_at)}</td></tr>
        <tr><td style="color:var(--text-muted)">Last Message</td><td>${esc(s.last_message_at)}</td></tr>
        <tr><td style="color:var(--text-muted)">Last Error</td><td>${esc(s.last_error)}</td></tr>
        <tr><td style="color:var(--text-muted)">Last Exit</td><td>${esc(s.last_exit_reason)}${s.last_exit_at ? ` at ${esc(s.last_exit_at)}` : ""}</td></tr>
        <tr><td style="color:var(--text-muted)">Stop Reason</td><td>${stopReasonLabel(s.stop_reason)}</td></tr>
        ${s.not_ready_reason ? `<tr><td style="color:var(--text-muted)">Not Ready</td><td>${esc(s.not_ready_reason)}</td></tr>` : ""}
        ${renderTransitions(s.recent_transitions)}
        <tr><td style="color:var(--text-muted)">Controls</td><td>${sourceButtonsHtml(s)}</td></tr>
      </table>`;
    }).join("<hr>");
  }

  function showSourcesMsg(text, ok) {
    const msg = $("sources-msg");
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
      const reason = t.reason ? ` (${esc(t.reason)})` : "";
      return `<div style="font-family:var(--mono);font-size:11px;color:var(--text-muted)">` +
        `${esc(at)} &nbsp; ${esc(t.from)} → ${esc(t.to)}${reason}</div>`;
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
      renderSourcesDetail(lastSourcesSnapshot || []);
      const res = await fetch(`/api/sources/${encodeURIComponent(name)}/${action}`,
                              { method: "POST" });
      let body = {};
      try { body = await res.json(); } catch { /* non-JSON */ }
      if (res.ok && body.ok) {
        if (action === "start") {
          showSourcesMsg(body.result === "already_running"
            ? "Feed already running — no duplicate started."
            : "Start requested — connecting…", true);
        } else if (action === "stop") {
          showSourcesMsg("Feed stopped. Credentials and subscriptions retained.", true);
        } else {
          showSourcesMsg("Restart requested — fresh authorize + reconnect…", true);
        }
      } else if (body.reason === "authentication_required") {
        showSourcesMsg("Daily login required.", false);
      } else if (body.reason === "unknown_source") {
        showSourcesMsg("Unknown source.", false);
      } else {
        showSourcesMsg(`Command failed (HTTP ${res.status}).`, false);
      }
    } catch {
      showSourcesMsg("Network error while controlling the feed.", false);
    } finally {
      sourceActionInFlight.delete(name);
      pollSources();   // immediate refresh of status + buttons
    }
  }

  function initSourceControls() {
    $("sources-detail").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-src-action]");
      if (!btn || btn.disabled) return;
      sourceControlRequest(btn.dataset.srcAction, btn.dataset.srcName);
    });
  }

  // ── Upstox auth (token submit) ──────────────────────────────────────────

  async function pollAuthStatus() {
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

  function initAuth() {
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

  function initCredentialSettings() {
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

  function initCredentialDelete() {
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


  // ── Subscribed-universe movers + inferred market status ─────────────────

  function renderMovers() {
    const body = $("movers-body");
    if (!body || quotes.size === 0) return;
    const all = [...quotes.values()].filter((q) => q.change_percent != null);
    if (!all.length) return;
    const byPct = [...all].sort((a, b) => b.change_percent - a.change_percent);
    const byVol = [...all].sort((a, b) => (b.volume ?? 0) - (a.volume ?? 0));
    const rows = [];
    const addRow = (cat, q) => rows.push(
      `<tr><td>${cat}</td><td>${esc(q.tradingsymbol) || "—"}</td>` +
      `<td>${q.ltp != null ? fmt(q.ltp) : "—"}</td>` +
      `<td class="${chgClass(q.change ?? 0)}">${fmt(q.change_percent)}%</td>` +
      `<td>${fmtVol(q.volume)}</td></tr>`);
    byPct.slice(0, 2).forEach((q) => addRow("Top Gainer", q));
    byPct.slice(-2).reverse().forEach((q) => { if (q.change_percent < 0) addRow("Top Loser", q); });
    byVol.slice(0, 2).forEach((q) => addRow("Volume Leader", q));
    body.innerHTML = rows.join("") ||
      '<tr><td colspan="5" class="empty-row">No data yet</td></tr>';
  }

  function renderMarketStatus() {
    const el = $("market-status-hint");
    if (!el) return;
    // Inferred from IST clock (NSE equity session Mon-Fri 09:15-15:30).
    // Clearly labelled as inferred — NOT broker-confirmed.
    const now = new Date();
    const ist = new Date(now.getTime() + (330 + now.getTimezoneOffset()) * 60000);
    const day = ist.getDay();
    const mins = ist.getHours() * 60 + ist.getMinutes();
    const open = day >= 1 && day <= 5 && mins >= 555 && mins <= 930;
    el.textContent = "Market status (inferred from IST time, not broker-confirmed): " +
      (open ? "Open" : "Closed");
  }
  // ── Quote details drawer ────────────────────────────────────────────────

  const fmtTs = (iso) => {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleTimeString(); } catch { return "—"; }
  };
  const fmtNum = (v) => v != null ? Number(v).toLocaleString("en-IN") : "—";
  const kvRow = (label, value) =>
    `<tr><td>${label}</td><td>${value ?? "—"}</td></tr>`;

  function renderDrawer(key) {
    const q = quotes.get(key);
    if (!q) return;
    $("drawer-title").textContent =
      (q.tradingsymbol || key.split(":").pop()) + "  ·  " + (q.exchange || "");

    $("drawer-price").innerHTML =
      kvRow("LTP", q.ltp != null ? fmt(q.ltp) : "—") +
      kvRow("Prev Close", q.close != null ? fmt(q.close) : "—") +
      kvRow("Change", q.change != null ? fmt(q.change) : "—") +
      kvRow("Change %", q.change_percent != null ? fmt(q.change_percent) + "%" : "—") +
      kvRow("Open", q.open != null ? fmt(q.open) : "—") +
      kvRow("High", q.high != null ? fmt(q.high) : "—") +
      kvRow("Low", q.low != null ? fmt(q.low) : "—") +
      kvRow("ATP", q.avg_trade_price != null ? fmt(q.avg_trade_price) : "—");

    $("drawer-trade").innerHTML =
      kvRow("Last Trade Qty", fmtNum(q.last_traded_qty)) +
      kvRow("Last Trade Time", fmtTs(q.last_trade_time)) +
      kvRow("Volume", fmtNum(q.volume)) +
      kvRow("Total Buy Qty", fmtNum(q.total_buy_qty)) +
      kvRow("Total Sell Qty", fmtNum(q.total_sell_qty));

    $("drawer-oi").innerHTML =
      kvRow("OI", fmtNum(q.open_interest)) +
      kvRow("Previous OI", fmtNum(q.previous_oi)) +
      kvRow("OI Change", fmtNum(q.oi_change)) +
      kvRow("OI Change %",
            q.oi_change_percent != null ? fmt(q.oi_change_percent) + "%" : "—");

    $("drawer-markets-section").innerHTML =
      kvRow("Bid", q.best_bid != null ? fmt(q.best_bid) : "—") +
      kvRow("Ask", q.best_ask != null ? fmt(q.best_ask) : "—") +
      kvRow("Upper Circuit", q.upper_circuit != null ? fmt(q.upper_circuit) : "—") +
      kvRow("Lower Circuit", q.lower_circuit != null ? fmt(q.lower_circuit) : "—") +
      kvRow("Exchange Time", fmtTs(q.exchange_ts)) +
      kvRow("Received", fmtTs(q.received_ts));

    const gk = q.greeks;
    if (gk && Object.values(gk).some((v) => v != null)) {
      $("drawer-greeks").innerHTML =
        `<table class="data-table kv-table">` +
        kvRow("Delta", gk.delta != null ? gk.delta.toFixed(4) : "—") +
        kvRow("Gamma", gk.gamma != null ? gk.gamma.toFixed(6) : "—") +
        kvRow("Theta", gk.theta != null ? gk.theta.toFixed(4) : "—") +
        kvRow("Vega", gk.vega != null ? gk.vega.toFixed(4) : "—") +
        kvRow("Rho", gk.rho != null ? gk.rho.toFixed(4) : "—") +
        kvRow("IV", gk.iv != null ? fmt(gk.iv) + "%" : "—") +
        `</table>`;
    } else {
      $("drawer-greeks").innerHTML = "<em>Greeks not available</em>";
    }

    // Depth fetched on demand — no continuous polling.
    $("drawer-depth").innerHTML = "<em>Loading…</em>";
    fetch(`/api/market/depth/${encodeURIComponent(q.exchange)}/${encodeURIComponent(q.instrument_token)}`)
      .then((r) => r.json())
      .then((d) => {
        const lvlRows = (levels) => levels.map((l) =>
          `<tr><td>${fmt(l.price)}</td><td>${fmtNum(l.quantity)}</td>` +
          `<td>${l.orders != null ? l.orders : "—"}</td></tr>`).join("");
        $("drawer-depth").innerHTML =
          `<table class="data-table"><thead><tr>` +
          `<th>Bid Px</th><th>Qty</th><th>Ord</th>` +
          `<th>Ask Px</th><th>Qty</th><th>Ord</th></tr></thead><tbody>` +
          lvlRows(d.bids || []) + lvlRows(d.asks || []) +
          `</tbody></table>`;
      })
      .catch(() => { $("drawer-depth").innerHTML = "<em>Depth unavailable</em>"; });
  }

  function openDrawer(key) {
    renderDrawer(key);
    $("quote-drawer").classList.remove("hidden");
  }
  function closeDrawer() {
    $("quote-drawer").classList.add("hidden");
  }

  function initDrawer() {
    $("drawer-close").addEventListener("click", closeDrawer);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeDrawer();
    });
    for (const bodyId of ["dash-body", "markets-body"]) {
      const body = $(bodyId);
      if (body) {
        body.addEventListener("click", (e) => {
          const row = e.target.closest("tr[data-key]");
          if (row) openDrawer(row.dataset.key);
        });
      }
    }
    // Refresh drawer content on live ticks while open.
    setInterval(() => {
      const title = $("drawer-title").textContent;
      if ($("quote-drawer").classList.contains("hidden") || title === "—") return;
      for (const [key] of quotes) {
        if (title.startsWith(key.split("|").pop().split(":").pop())) {
          renderDrawer(key);
          break;
        }
      }
    }, 5000);
  }

  // ── Market filter (Markets page) ────────────────────────────────────────

  function initFilter() {
    $("market-filter").addEventListener("input", (e) => {
      const term = e.target.value.toLowerCase();
      mktRows.forEach((row, key) => {
        const sym = row.querySelector(".sym").textContent.toLowerCase();
        row.style.display = sym.includes(term) ? "" : "none";
      });
    });
  }

  // ── Initial snapshot fetch ──────────────────────────────────────────────

  async function loadInitialQuotes() {
    try {
      const res = await fetch("/api/market/quotes");
      const data = await res.json();
      if (data.quotes) for (const q of data.quotes) handleQuoteUpdate(q);
    } catch { /* SSE will populate */ }
  }

  // ── Init ────────────────────────────────────────────────────────────────


  // ── Instruments search + sync ───────────────────────────────────────────

  function initInstruments() {
    const input = $("instr-search");
    const msg = $("instr-sync-msg");
    let debounce = null;
    const doSearch = async () => {
      const q = input.value.trim();
      const exchange = $("instr-exchange").value;
      const url = "/api/instruments/search?limit=25" +
        (q ? "&q=" + encodeURIComponent(q) : "") +
        (exchange ? "&exchange=" + exchange : "");
      try {
        const res = await fetch(url);
        const data = await res.json();
        const body = $("instr-body");
        if (!data.results || !data.results.length) {
          body.innerHTML = '<tr><td colspan="9" class="empty-row">No matches. Sync a provider master first if the catalog is empty.</td></tr>';
          return;
        }
        body.innerHTML = data.results.map((r) =>
          `<tr data-tok="${r.instrument_token}" data-ex="${esc(r.exchange)}" data-sym="${esc(r.tradingsymbol)}">` +
          `<td>${esc(r.tradingsymbol)}</td><td>${esc(r.name) || "—"}</td>` +
          `<td>${esc(r.exchange)}</td><td>${esc(r.instrument_type) || "—"}</td>` +
          `<td>${esc(r.expiry) || "—"}</td><td>${r.strike != null ? r.strike : "—"}</td>` +
          `<td>${r.lot_size != null ? r.lot_size : "—"}</td><td>${esc(r.provider)}</td>` +
          `<td><button class="btn wl-add" style="padding:2px 8px;font-size:11px">+ Watchlist</button></td></tr>`
        ).join("");
      } catch { /* silent */ }
    };
    input.addEventListener("input", () => {
      clearTimeout(debounce);
      debounce = setTimeout(doSearch, 300);
    });
    $("instr-exchange").addEventListener("change", doSearch);
    document.getElementById("instr-table").addEventListener("click", async (e) => {
      const btn = e.target.closest(".wl-add");
      if (!btn) return;
      const tr = btn.closest("tr");
      try {
        let wlId = null;
        const res = await fetch("/api/watchlists");
        const data = await res.json();
        if (data.watchlists && data.watchlists.length) {
          wlId = data.watchlists[0].id;
        } else {
          const created = await fetch("/api/watchlists", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: "Default" }) });
          const cd = await created.json();
          wlId = cd.watchlist.id;
        }
        await fetch(`/api/watchlists/${wlId}/items`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ exchange: tr.dataset.ex,
            instrument_token: tr.dataset.tok,
            tradingsymbol: tr.dataset.sym }) });
        msg.textContent = `Added ${tr.dataset.sym} to watchlist.`;
        msg.className = "hint ok";
        loadWatchlists();
      } catch {
        msg.textContent = "Failed to add to watchlist.";
        msg.className = "hint err";
      }
    });
    const doSync = async (provider, btn) => {
      btn.disabled = true;
      msg.textContent = `Syncing ${provider} instrument master…`;
      msg.className = "hint";
      try {
        const res = await fetch("/api/instruments/sync", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider }) });
        const data = await res.json();
        msg.textContent = res.ok
          ? `${provider} sync complete: ${data.records} instruments.`
          : (data.error || "Sync failed.");
        msg.className = "hint " + (res.ok ? "ok" : "err");
        doSearch();
        if (typeof loadSyncState === "function") loadSyncState();
      } catch {
        msg.textContent = "Network error during sync.";
        msg.className = "hint err";
      } finally { btn.disabled = false; }
    };
    $("instr-sync-upstox").addEventListener("click",
      (e) => doSync("upstox", e.target));
    $("instr-sync-fyers").addEventListener("click",
      (e) => doSync("fyers", e.target));

    async function loadSyncState() {
      try {
        const res = await fetch("/api/instruments/sync-state");
        const d = await res.json();
        const parts = (d.providers || []).map((p) =>
          `${p.provider}: ${p.instruments} instruments` +
          (p.last_sync ? ` (synced ${new Date(p.last_sync).toLocaleString()})`
                       : " (never synced)"));
        $("instr-sync-state").textContent = parts.length
          ? "Catalog — " + parts.join(" | ")
          : "Catalog empty — sync a provider master to enable search.";
      } catch { /* silent */ }
    }
    loadSyncState();

    doSearch();
  }

  // ── Watchlists ──────────────────────────────────────────────────────────

  let currentWatchlistId = null;

  async function loadWatchlists() {
    try {
      const res = await fetch("/api/watchlists");
      const data = await res.json();
      const sel = $("wl-select");
      if (sel) sel.innerHTML = (data.watchlists || []).map((w) =>
        `<option value="${w.id}">${esc(w.name)}</option>`).join("");
      currentWatchlistId = sel && sel.value ? Number(sel.value) : null;
      renderWatchlistItems(data.watchlists || []);
    } catch { /* silent */ }
  }

  function renderWatchlistItems(watchlists) {
    const wl = (watchlists || []).find(
      (w) => String(w.id) === String(currentWatchlistId));
    const body = $("wl-body");
    if (!wl || !wl.items.length) {
      body.innerHTML = '<tr><td colspan="8" class="empty-row">No items. Add instruments from the Instruments page.</td></tr>';
      return;
    }
    body.innerHTML = wl.items.map((it) => {
      const q = quotes.get(it.instrument_token)
        || quotes.get(it.exchange + ":" + it.instrument_token);
      const ltp = q && q.ltp != null ? fmt(q.ltp) : "—";
      const chg = q && q.change != null ? fmt(q.change) : "—";
      const pct = q && q.change_percent != null
        ? fmt(q.change_percent) + "%" : "—";
      const cls = q ? chgClass(q.change ?? 0) : "";
      return `<tr data-item-id="${it.id}" data-token="${it.instrument_token}" data-ex="${esc(it.exchange)}">` +
        `<td>${esc(it.tradingsymbol)}</td><td>${ltp}</td>` +
        `<td class="${cls}">${chg}</td><td class="${cls}">${pct}</td>` +
        `<td>${q ? fmtVol(q.volume) : "—"}</td>` +
        `<td>${q && q.best_bid != null ? fmt(q.best_bid) : "—"}</td>` +
        `<td>${q && q.best_ask != null ? fmt(q.best_ask) : "—"}</td>` +
        `<td><button class="btn wl-remove" style="padding:2px 8px;font-size:11px">✕</button></td></tr>`;
    }).join("");
  }

  function initWatchlists() {
    $("wl-create").addEventListener("click", async () => {
      const name = $("wl-new-name").value.trim();
      if (!name) return;
      await fetch("/api/watchlists", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }) });
      $("wl-new-name").value = "";
      loadWatchlists();
    });
    $("wl-rename").addEventListener("click", async () => {
      const name = $("wl-new-name").value.trim();
      if (!name || !currentWatchlistId) return;
      await fetch(`/api/watchlists/${currentWatchlistId}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }) });
      $("wl-new-name").value = "";
      loadWatchlists();
    });
    $("wl-delete").addEventListener("click", async () => {
      if (!currentWatchlistId ||
          !confirm("Delete this watchlist and its items?")) return;
      await fetch(`/api/watchlists/${currentWatchlistId}`,
        { method: "DELETE" });
      loadWatchlists();
    });
    $("wl-select").addEventListener("change", loadWatchlists);
    $("wl-table").addEventListener("click", async (e) => {
      const btn = e.target.closest(".wl-remove");
      if (!btn) return;
      const itemId = btn.closest("tr").dataset.itemId;
      await fetch(`/api/watchlists/items/${itemId}`, { method: "DELETE" });
      loadWatchlists();
    });
    setInterval(loadWatchlists, 10000);   // refresh live values from SSE state
    loadWatchlists();
  }

  // ── Option chain ────────────────────────────────────────────────────────

  function initOptionChain() {
    const search = $("oc-underlying-search");
    const sel = $("oc-underlying-select");
    const expSel = $("oc-expiry-select");
    const msg = $("oc-message");

    let debounce = null;
    search.addEventListener("input", () => {
      clearTimeout(debounce);
      debounce = setTimeout(async () => {
        const q = search.value.trim();
        if (!q) return;
        try {
          const res = await fetch("/api/options/underlyings?q=" +
            encodeURIComponent(q));
          const d = await res.json();
          sel.innerHTML = '<option value="">Underlying…</option>' +
            (d.underlyings || []).map((u) =>
              `<option value="${u}">${u}</option>`).join("");
        } catch { /* silent */ }
      }, 300);
    });

    sel.addEventListener("change", async () => {
      ocUnderlying = sel.value;
      expSel.innerHTML = '<option value="">Expiry…</option>';
      expSel.disabled = true;
      if (!ocUnderlying) return;
      try {
        const res = await fetch("/api/options/expiries?underlying=" +
          encodeURIComponent(ocUnderlying));
        const d = await res.json();
        expSel.innerHTML = '<option value="">Expiry…</option>' +
          (d.expiries || []).map((e) =>
            `<option value="${e}">${e}</option>`).join("");
        expSel.disabled = !(d.expiries || []).length;
      } catch { /* silent */ }
    });

    $("oc-load").addEventListener("click", async () => {
      const expiry = expSel.value;
      msg.textContent = "";
      msg.className = "hint";
      if (!ocUnderlying || !expiry) {
        msg.textContent = "Pick an underlying and expiry first.";
        msg.className = "hint err";
        return;
      }
      // Resolve the underlying's instrument key from catalog search.
      try {
        const sres = await fetch("/api/instruments/search?q=" +
          encodeURIComponent(ocUnderlying) + "&limit=5");
        const sd = await sres.json();
        const hit = (sd.results || []).find(
          (r) => r.name === ocUnderlying || r.tradingsymbol === ocUnderlying)
          || (sd.results || [])[0];
        if (!hit) {
          msg.textContent = "Underlying not found in catalog.";
          msg.className = "hint err";
          return;
        }
        const res = await fetch("/api/options/chain?instrument_key=" +
          encodeURIComponent(hit.instrument_token) + "&exchange=" +
          encodeURIComponent(hit.exchange) + "&tradingsymbol=" +
          encodeURIComponent(hit.tradingsymbol) + "&expiry=" + expiry);
        const d = await res.json();
        if (!res.ok) {
          msg.textContent = d.error || "Chain load failed.";
          msg.className = "hint err";
          return;
        }
        $("oc-spot").textContent = d.spot_price != null
          ? fmt(d.spot_price) : "—";
        $("oc-atm").textContent = d.atm_strike != null
          ? fmt(d.atm_strike) : "—";
        ocFullStrikes = d.strikes || [];
        renderOcStrikes();
      } catch {
        msg.textContent = "Network error loading chain.";
        msg.className = "hint err";
      }
    });
  }

function renderOcStrikes() {
    const win = Number($("oc-window").value) || 0;
    let rows = ocFullStrikes;
    if (win > 0 && ocFullStrikes.length) {
      const atmIdx = ocFullStrikes.findIndex((s) => s.atm);
      const center = atmIdx >= 0 ? atmIdx : Math.floor(rows.length / 2);
      rows = ocFullStrikes.slice(Math.max(0, center - win),
                                 center + win + 1);
    }
    const side = (x) => x ? [
      fmtVol(x.oi), fmtNum(x.oi_change), fmtVol(x.volume),
      x.iv != null ? fmt(x.iv) : "—",
      x.ltp != null ? fmt(x.ltp) : "—",
      (x.close != null && x.ltp != null) ? fmt(x.ltp - x.close) : "—",
    ].map((v) => `<td>${v}</td>`).join("") : "<td>—</td>".repeat(6);
    $("oc-body").innerHTML = rows.map((s) => {
      const rowCls = s.atm ? ' style="background:var(--accent-dim)"' : "";
      return `<tr${rowCls}>` + side(s.call) +
        `<td><b>${fmt(s.strike)}</b></td>` + side(s.put) + "</tr>";
    }).join("");
  }

  // ── Charts (ECharts candlestick + volume) ───────────────────────────────

  let chartSelection = null;   // {instrument_key, exchange, tradingsymbol}

  function initCharts() {
    const search = $("chart-search");
    const sel = $("chart-instrument-select");
    let debounce = null;

    search.addEventListener("input", () => {
      clearTimeout(debounce);
      debounce = setTimeout(async () => {
        const q = search.value.trim();
        if (!q) return;
        try {
          const res = await fetch("/api/instruments/search?limit=15&q=" +
            encodeURIComponent(q));
          const d = await res.json();
          sel.innerHTML = '<option value="">Instrument…</option>' +
            (d.results || []).map((r) =>
              `<option value="${r.instrument_token}" data-ex="${esc(r.exchange)}"` +
              ` data-sym="${esc(r.tradingsymbol)}">` +
              `${esc(r.tradingsymbol)} (${esc(r.exchange)})</option>`).join("");
        } catch { /* silent */ }
      }, 300);
    });

    sel.addEventListener("change", () => {
      const opt = sel.selectedOptions[0];
      chartSelection = opt && opt.value ? {
        instrument_key: opt.value,
        exchange: opt.dataset.ex,
        tradingsymbol: opt.dataset.sym,
      } : null;
    });

    $("chart-load").addEventListener("click", async () => {
      const unit = $("chart-unit").value;
      const interval = $("chart-interval").value || 1;
      const days = Number($("chart-range").value) || 30;
      const provider = $("chart-provider").value;
      const msg = $("chart-message");
      if (!chartSelection) {
        msg.textContent = "Search and select an instrument first.";
        msg.className = "hint err";
        return;
      }
      const to = new Date().toISOString().slice(0, 10);
      const from = new Date(Date.now() - days * 86400000)
        .toISOString().slice(0, 10);
      msg.textContent = "Loading history…";
      msg.className = "hint";
      try {
        const res = await fetch("/api/market/history?instrument_key=" +
          encodeURIComponent(chartSelection.instrument_key) +
          "&provider=" + provider +
          "&unit=" + unit + "&interval=" + interval +
          "&from=" + from + "&to=" + to);
        const d = await res.json();
        if (!res.ok) {
          msg.textContent = d.error || "History load failed.";
          msg.className = "hint err";
          return;
        }
        if (!d.candles || !d.candles.length) {
          msg.textContent = "No history data returned for this range.";
          msg.className = "hint err";
          return;
        }
        msg.textContent = `${d.candles.length} candles loaded.`;
        msg.className = "hint ok";
        renderChart(d.candles);
      } catch {
        msg.textContent = "Network error loading history.";
        msg.className = "hint err";
      }
    });
  }

  function sma(values, period) {
    // Simple presentation-derived moving average over canonical closes.
    const out = [];
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
      sum += values[i];
      if (i >= period) sum -= values[i - period];
      out.push(i >= period - 1 ? +(sum / period).toFixed(4) : null);
    }
    return out;
  }

  function renderChart(candles) {
    if (!window.echarts) {
      $("chart-message").textContent = "Chart library not loaded.";
      return;
    }
    if (!chartInstance) {
      chartInstance = echarts.init($("chart-container"));
    }
    const times = candles.map((c) =>
      c.timestamp.slice(0, 16).replace("T", " "));
    const closes = candles.map((c) => c.close);
    const kline = candles.map((c) => [c.open, c.close, c.low, c.high]);
    const vols = candles.map((c) => c.volume ?? 0);
    chartInstance.setOption({
      animation: false,
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      legend: { data: ["SMA20", "SMA50"], top: 0 },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [{ left: 60, right: 20, top: 24, height: "56%" },
             { left: 60, right: 20, top: "72%", height: "18%" }],
      xAxis: [
        { type: "category", data: times },
        { type: "category", gridIndex: 1, data: times,
          axisLabel: { show: false } },
      ],
      yAxis: [
        { scale: true },
        { gridIndex: 1, axisLabel: { show: false } },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1] },
        { type: "slider", xAxisIndex: [0, 1], top: "92%" },
      ],
      series: [
        { type: "candlestick", name: "Price", data: kline,
          itemStyle: { color: "#3fb950", color0: "#f85149",
                       borderColor: "#3fb950", borderColor0: "#f85149" } },
        { type: "line", name: "SMA20", data: sma(closes, 20),
          showSymbol: false, lineStyle: { width: 1 } },
        { type: "line", name: "SMA50", data: sma(closes, 50),
          showSymbol: false, lineStyle: { width: 1 } },
        { type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: vols },
      ],
    });
  }

  // ── Alerts ──────────────────────────────────────────────────────────────

  function initAlerts() {
    $("alert-create").addEventListener("click", async () => {
      const token = $("alert-token").value.trim();
      const symbol = $("alert-symbol").value.trim() || token;
      const field = $("alert-field").value;
      const operator = $("alert-operator").value;
      const threshold = parseFloat($("alert-threshold").value);
      if (!token || isNaN(threshold)) return;
      await fetch("/api/alerts", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ exchange: "NSE",
          instrument_token: token, tradingsymbol: symbol,
          field, operator, threshold }) });
      $("alert-threshold").value = "";
      loadAlerts();
    });
    $("alerts-table").addEventListener("click", async (e) => {
      const row = e.target.closest("tr[data-alert-id]");
      if (!row) return;
      const id = row.dataset.alertId;
      if (e.target.classList.contains("alert-rearm")) {
        await fetch(`/api/alerts/${id}/rearm`, { method: "POST" });
      } else if (e.target.classList.contains("alert-toggle")) {
        await fetch(`/api/alerts/${id}/enabled`, { method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            enabled: e.target.dataset.enabled !== "true" }) });
      } else if (e.target.classList.contains("alert-delete")) {
        await fetch(`/api/alerts/${id}`, { method: "DELETE" });
      } else { return; }
      loadAlerts();
    });
    loadAlerts();
    setInterval(loadAlerts, 15000);
    const clearBtn = $("alert-history-clear");
    if (clearBtn) {
      clearBtn.addEventListener("click", async () => {
        if (!confirm("Clear all alert trigger history? This cannot be undone."))
          return;
        await fetch("/api/alerts/history", { method: "DELETE" });
        loadAlertHistory();
      });
    }
    loadAlertHistory();
    setInterval(loadAlertHistory, 15000);
  }

  async function loadAlertHistory() {
    const body = $("alert-history-body");
    if (!body) return;
    try {
      const res = await fetch("/api/alerts/history?limit=50");
      const data = await res.json();
      const rows = data.history || [];
      if (!rows.length) {
        body.innerHTML =
          '<tr><td colspan="6" class="empty-row">No trigger history yet.</td></tr>';
        return;
      }
      body.innerHTML = rows.map((h) => {
        const cond = `${h.field || ""} ${h.operator === "gt" ? ">" :
          h.operator === "lt" ? "<" : (h.operator || "").replace("crosses_", "crosses ")} ${h.threshold ?? ""}`;
        const t = (h.triggered_at || "").replace("T", " ").slice(0, 19);
        const prov = h.provider ? `<span class="chip">${h.provider}</span>` : "—";
        return `<tr>` +
          `<td>${t}</td>` +
          `<td>${h.tradingsymbol || ""}</td>` +
          `<td>${cond}</td>` +
          `<td>${h.observed_value ?? "—"}</td>` +
          `<td>${prov}</td>` +
          `<td>${h.exchange || ""}</td></tr>`;
      }).join("");
    } catch { /* silent */ }
  }

  async function loadAlerts() {
    try {
      const res = await fetch("/api/alerts");
      const data = await res.json();
      const body = $("alerts-body");
      const alerts = data.alerts || [];
      if (!alerts.length) {
        body.innerHTML = '<tr><td colspan="5" class="empty-row">No alerts configured.</td></tr>';
      } else {
        body.innerHTML = alerts.map((a) => {
          const cond = `${a.field} ${a.operator === "gt" ? ">" : "<"} ${a.threshold}`;
          const stateCls = a.state === "triggered" ? "chip chip-off" :
            (a.enabled ? "chip chip-on" : "chip");
          return `<tr data-alert-id="${a.id}">` +
            `<td>${esc(a.tradingsymbol)}</td><td>${cond}</td>` +
            `<td><span class="${stateCls}">${a.state}</span></td>` +
            `<td><button class="btn alert-toggle" data-enabled="${a.enabled ? "true" : "false"}" style="padding:2px 8px;font-size:11px">${a.enabled ? "On" : "Off"}</button></td>` +
            `<td><button class="btn alert-rearm" style="padding:2px 8px;font-size:11px">Re-arm</button> ` +
            `<button class="btn alert-delete" style="padding:2px 8px;font-size:11px;border-color:var(--red);color:var(--red)">✕</button></td></tr>`;
        }).join("");
      }
      const notes = data.notifications || [];
      $("alert-notifications").innerHTML = notes.length
        ? '<div class="panel"><div class="panel-header"><h2>Triggered</h2></div>' +
          notes.map((n) => `<div class="hint err">${esc(n.tradingsymbol)}: ${n.field} ${n.operator === "gt" ? ">" : "<"} ${n.threshold} (now ${n.value})</div>`).join("") +
          "</div>"
        : "";
    } catch { /* silent */ }
  }

function initAlertPush() {
    if (alertPushSource) return;   // never duplicate
    try {
      alertPushSource = new EventSource("/events/stream");
      alertPushSource.onmessage = (e) => {
        let envelope;
        try { envelope = JSON.parse(e.data); } catch { return; }
        const type = typeof envelope === "string"
          ? JSON.parse(envelope).type : envelope.type;
        if (type === "source.state_changed") {
          // A source changed state (start/stop/reconnect/auth) — refresh the
          // status chips and detail panel immediately (WP22).
          pollSources();
          return;
        }
        if (type !== "alert.triggered") return;
        const d = typeof envelope.data === "string"
          ? JSON.parse(envelope.data) : envelope.data;
        pushAlertNotification(d);
      };
      alertPushSource.onerror = () => { /* EventSource auto-reconnects */ };
    } catch { /* silent */ }
  }

// ==== Chat (AI assistant over MarketHub tools) ====
  let chatHistory = [];   // client-side session only

  function chatAppend(role, text) {
    const box = document.getElementById("chat-messages");
    if (!box) return null;
    const div = document.createElement("div");
    div.className = "hint " + (role === "user" ? "" : "ok");
    div.style.whiteSpace = "pre-wrap";
    div.style.marginBottom = "6px";
    div.textContent = (role === "user" ? "You: " : "Assistant: ") + text;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
    return div;
  }

  function chatActivity(text) {
    const el = document.getElementById("chat-activity");
    if (el) el.textContent = text || "";
  }

  async function initChat() {
    const input = document.getElementById("chat-input");
    const send = document.getElementById("chat-send");
    const clear = document.getElementById("chat-clear");
    if (!input || !send) return;

    try {
      const st = await (await fetch("/api/chat/status")).json();
      const chip = document.getElementById("chat-provider-chip");
      if (chip) {
        chip.textContent = st.configured
          ? ("AI: " + st.model) : "AI not configured";
        chip.className = "chip " + (st.configured ? "chip-on" : "chip-off");
      }
    } catch { /* status optional */ }

    if (clear) clear.addEventListener("click", () => {
      chatHistory = [];
      const box = document.getElementById("chat-messages");
      if (box) box.innerHTML = "";
      chatActivity("");
    });

    async function sendMsg() {
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      send.disabled = true;
      const errEl = document.getElementById("chat-error");
      errEl.style.display = "none";
      chatAppend("user", text);
      let assistantText = "";
      const live = chatAppend("assistant", "\u2026");
      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: text, history: chatHistory }),
        });
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          throw new Error(d.error || ("HTTP " + res.status));
        }
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const chunk = await reader.read();
          if (chunk.done) break;
          buf += dec.decode(chunk.value, { stream: true });
          let idx;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            const raw = buf.slice(0, idx); buf = buf.slice(idx + 2);
            if (!raw.startsWith("data:")) continue;
            let ev;
            try { ev = JSON.parse(raw.slice(5).trim()); } catch { continue; }
            if (ev.type === "tool_start") {
              chatActivity("using tool: " + ev.name);
            } else if (ev.type === "delta") {
              assistantText += ev.text;
              live.textContent = "Assistant: " + assistantText;
              chatActivity("");
            } else if (ev.type === "error") {
              errEl.textContent = ev.message;
              errEl.style.display = "block";
            }
          }
        }
        chatHistory.push({ role: "user", content: text });
        if (assistantText) {
          chatHistory.push({ role: "assistant", content: assistantText });
        }
        live.textContent = "Assistant: " +
          (assistantText || "(no answer)");
      } catch (e) {
        live.textContent = "Assistant: (failed)";
        errEl.textContent = String(e.message || e);
        errEl.style.display = "block";
      } finally {
        send.disabled = false;
        input.focus();
      }
    }

    send.addEventListener("click", sendMsg);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") sendMsg();
    });
  }

function pushAlertNotification(d) {
    const notes = $("alert-notifications");
    if (!notes) return;
    const time = new Date().toLocaleTimeString();
    const cond = `${d.field} ${d.operator === "gt" ? ">" :
      d.operator === "lt" ? "<" : d.operator.replace("crosses_", "crosses ")} ${d.threshold}`;
    const div = document.createElement("div");
    div.className = "hint err";
    div.textContent =
      `${time} — ${esc(d.tradingsymbol)}: ${cond} (now ${d.observed_value})`;
    notes.prepend(div);
    while (notes.children.length > 20) notes.removeChild(notes.lastChild);
  }

function initBackup() {
    const btn = $("db-backup");
    if (!btn) return;
    const msg = $("backup-message");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const res = await fetch("/api/admin/backup", { method: "POST" });
        const d = await res.json();
        if (res.ok) {
          msg.textContent = "Backup saved to data/backups/" + d.file +
            " (contains ciphertext only; master.key required to decrypt).";
          msg.className = "hint ok";
        } else {
          msg.textContent = d.error || "Backup failed.";
          msg.className = "hint err";
        }
      } catch {
        msg.textContent = "Network error during backup.";
        msg.className = "hint err";
      } finally { btn.disabled = false; }
    });
  }

  function initFyers() {
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
        loginBtn.style.display = d.login_available ? "" : "none";
        // Feed runtime state comes from the source manager, not auth.
        try {
          const sres = await fetch("/api/sources/status");
          const sd = await sres.json();
          const src = (sd.sources || []).find(
            (s) => s.name === "fyers" || (s.type || "").indexOf("fyers") >= 0);
          $("fyers-feed-state").textContent =
            src ? friendlyState(src.state || "unknown") : "source not configured";
        } catch { /* keep placeholder */ }
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
        const res = await fetch("/api/settings/fyers", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ app_id: appId, secret_id: secret }) });
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

    refresh();
  }

  function initAppSettings() {
    const saveBtn = $("app-save");
    if (!saveBtn) return;
    const msg = $("app-message");

    async function refresh() {
      try {
        const res = await fetch("/api/settings/app");
        const d = await res.json();
        $("app-base-url").textContent = d.public_base_url || "—";
        $("app-base-url").className =
          "chip " + (d.public_base_url ? "chip-on" : "chip-off");
        $("app-fyers-callback").textContent = d.fyers_callback_url || "—";
        $("app-base-url-input").value = d.public_base_url || "";
      } catch { /* silent */ }
    }

    saveBtn.addEventListener("click", async () => {
      const base = $("app-base-url-input").value.trim();
      msg.textContent = "";
      msg.className = "hint";
      if (!base) {
        msg.textContent = "Public Base URL is required.";
        msg.className = "hint err";
        return;
      }
      saveBtn.disabled = true;
      try {
        const res = await fetch("/api/settings/app", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ public_base_url: base }) });
        const d = await res.json();
        if (res.ok && d.status === "ok") {
          msg.textContent = "Saved. Restart MarketHub to apply.";
          msg.className = "hint ok";
          refresh();
        } else {
          msg.textContent = d.error || "Failed to save application settings.";
          msg.className = "hint err";
        }
      } catch {
        msg.textContent = "Network error saving application settings.";
        msg.className = "hint err";
      } finally { saveBtn.disabled = false; }
    });

    refresh();
  }

  function init() {
    initTheme();
    initNav();
    initFilter();
    initAuth();
    initCredentialSettings();
    initCredentialDelete();
    initDrawer();
    initInstruments();
    initWatchlists();
    initOptionChain();
    initCharts();
    initAlerts();
    initBackup();
    initFyers();
    initAppSettings();
    initChat();
    initAlertPush();
    initSourceControls();
    loadInitialQuotes();
    connectSSE();
    pollSources();                     // immediate status render (no 10s wait)
    setInterval(pollSources, 10000);   // then poll source status every 10 s
    pollAuthStatus();                  // auth snapshot feeds Sources controls
    setInterval(pollAuthStatus, 10000);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
