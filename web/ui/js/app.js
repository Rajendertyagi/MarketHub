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
    loadInitialQuotes();
    loadSources();
    connectSSE();
    setInterval(pollSources, 10000);   // poll source status every 10 s
  }

  document.addEventListener("DOMContentLoaded", init);
})();
