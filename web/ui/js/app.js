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
    switchView("dashboard");
  }

  function switchView(view) {
    currentView = view;
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    const el = $("view-" + view);
    if (el) el.classList.add("active");
    document.querySelectorAll(".nav-link").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === view);
    });
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

    $("chip-last-update").textContent = nowStr();
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
      row.innerHTML = `
        <td class="sym"></td><td class="ltp"></td><td class="chg"></td>
        <td class="pct"></td><td class="open"></td><td class="high"></td>
        <td class="low"></td><td class="close"></td><td class="vol"></td>
        <td class="oi"></td><td class="bid"></td><td class="ask"></td>
        <td class="atp"></td><td class="upd"></td>`;
      const body = $("markets-body");
      if (body.querySelector(".empty-row")) body.innerHTML = "";
      body.appendChild(row);
      mktRows.set(key, row);
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
    row.querySelector(".oi").textContent = fmtVol(q.open_interest);
    row.querySelector(".bid").textContent = fmt(q.best_bid);
    row.querySelector(".ask").textContent = fmt(q.best_ask);
    row.querySelector(".atp").textContent = fmt(q.avg_trade_price);
    row.querySelector(".upd").textContent =
      q.received_ts ? new Date(q.received_ts).toLocaleTimeString() : "—";
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

      // Update status chips.
      for (const s of sources) {
        const st = s.state || "unknown";
        $("chip-upstox").textContent = st;
        $("chip-upstox").className = "chip " +
          (st === "streaming" ? "chip-on" : st === "failed" ? "chip-off" : "");
        $("chip-instruments").textContent = s.configured_instruments ?? 0;
        $("chip-reconnects").textContent = s.reconnect_count ?? 0;
        $("broker-indicator").textContent = "● " + st;
        $("broker-indicator").className = "indicator " +
          (st === "streaming" ? "indicator-on" : "indicator-off");
      }

      // Update sources detail panel (only when visible).
      if (currentView === "sources") {
        renderSourcesDetail(sources);
      }
    } catch { /* silent */ }
  }

  function renderSourcesDetail(sources) {
    const el = $("sources-detail");
    if (!sources.length) { el.innerHTML = "<em>No sources configured</em>"; return; }
    el.innerHTML = sources.map((s) => `
      <table class="data-table">
        <tr><td style="width:180px;color:var(--text-muted)">Name</td><td>${s.name || "—"}</td></tr>
        <tr><td style="color:var(--text-muted)">State</td><td><span class="src-state">${s.state || "—"}</span></td></tr>
        <tr><td style="color:var(--text-muted)">Mode</td><td>${s.mode || "—"}</td></tr>
        <tr><td style="color:var(--text-muted)">Instruments</td><td>${s.configured_instruments ?? 0}</td></tr>
        <tr><td style="color:var(--text-muted)">Connect Attempts</td><td>${s.connect_attempts ?? 0}</td></tr>
        <tr><td style="color:var(--text-muted)">Reconnects</td><td>${s.reconnect_count ?? 0}</td></tr>
        <tr><td style="color:var(--text-muted)">Frames Received</td><td>${s.frames_received ?? 0}</td></tr>
        <tr><td style="color:var(--text-muted)">Last Connected</td><td>${s.last_connected_at || "—"}</td></tr>
        <tr><td style="color:var(--text-muted)">Last Message</td><td>${s.last_message_at || "—"}</td></tr>
        <tr><td style="color:var(--text-muted)">Last Error</td><td>${s.last_error || "—"}</td></tr>
      </table>
    `).join("<hr>");
  }

  // ── Upstox auth (token submit) ──────────────────────────────────────────

  async function pollAuthStatus() {
    try {
      const res = await fetch("/api/auth/upstox/status");
      const d = await res.json();
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
      } else if (!d.token_configured) {
        label = "Authentication Required"; cls = "chip chip-off";
      } else if (d.expired === true) {
        label = "Token Expired"; cls = "chip chip-off";
      } else if (d.expiry_known) {
        label = "Active"; cls = "chip chip-on";
      } else {
        label = "Configured"; cls = "chip chip-on";
      }
      chip.textContent = label;
      chip.className = cls;
      $("auth-feed-state").textContent = d.state || "—";
    } catch { /* silent */ }
  }

  function handleAuthCallbackParam() {
    const params = new URLSearchParams(window.location.search);
    const auth = params.get("auth");
    if (!auth) return;
    const msg = $("auth-message");
    if (auth === "ok") {
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
    const qs = params.toString();
    history.replaceState(null, "", window.location.pathname + (qs ? "?" + qs : ""));
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

  function init() {
    initTheme();
    initNav();
    initFilter();
    initAuth();
    initCredentialSettings();
    initCredentialDelete();
    loadInitialQuotes();
    connectSSE();
    pollSources();                     // immediate status render (no 10s wait)
    setInterval(pollSources, 10000);   // then poll source status every 10 s
  }

  document.addEventListener("DOMContentLoaded", init);
})();
