"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  let es = null;
  const quotes = new Map();

  function setConn(connected, text) {
    $("conn-status").className = "status-dot " + (connected ? "connected" : "disconnected");
    $("conn-text").textContent = text;
  }

  function fmt(v, dp = 2) {
    return v != null ? Number(v).toFixed(dp) : "—";
  }

  function renderQuotes() {
    const body = $("quotes-body");
    if (!quotes.size) { body.innerHTML = '<tr><td colspan="12"><em>waiting…</em></td></tr>'; return; }
    let html = "";
    for (const [key, q] of quotes) {
      const chg = q.change, pct = q.change_percent;
      const cls = chg > 0 ? "up" : chg < 0 ? "down" : "";
      html += `<tr>
        <td>${q.tradingsymbol || key}</td>
        <td>${fmt(q.ltp)}</td>
        <td class="${cls}">${fmt(q.change)}</td>
        <td class="${cls}">${pct != null ? fmt(pct) + "%" : "—"}</td>
        <td>${fmt(q.open)}</td><td>${fmt(q.high)}</td>
        <td>${fmt(q.low)}</td><td>${fmt(q.close)}</td>
        <td>${q.volume ?? "—"}</td>
        <td>${fmt(q.best_bid)}</td><td>${fmt(q.best_ask)}</td>
        <td>${q.received_ts ? new Date(q.received_ts).toLocaleTimeString() : "—"}</td>
      </tr>`;
    }
    body.innerHTML = html;
    $("last-update").textContent = "Last update: " + new Date().toLocaleTimeString();
  }

  function handleQuote(data) {
    const key = data.exchange + ":" + data.instrument_token;
    const prev = quotes.get(key);
    quotes.set(key, data);
    renderQuotes();
  }

  function connectSSE() {
    es = new EventSource("/api/market/stream");
    es.onopen = () => setConn(true, "live");
    es.onerror = () => { setConn(false, "reconnecting…"); };
    es.addEventListener("quote", (e) => {
      try {
        const envelope = JSON.parse(e.data);
        if (envelope.type === "quote" && envelope.data) handleQuote(envelope.data);
      } catch (err) { console.warn("bad SSE payload", err); }
    });
  }

  async function loadSources() {
    try {
      const res = await fetch("/api/sources/status");
      const data = await res.json();
      const el = $("sources-list");
      if (!data.sources || !data.sources.length) { el.innerHTML = "<em>no sources configured</em>"; return; }
      el.innerHTML = data.sources.map((s) =>
        `<div><span class="src-state">${s.state || s.status?.state || "unknown"}</span> — ${s.name || "source"} (${s.mode || "?"})</div>`
      ).join("");
    } catch (err) { $("sources-list").innerHTML = "<em>unavailable</em>"; }
  }

  async function loadInitialQuotes() {
    try {
      const res = await fetch("/api/market/quotes");
      const data = await res.json();
      if (data.quotes) for (const q of data.quotes) handleQuote(q);
    } catch (err) { /* SSE will populate */ }
  }

  // -- init --
  loadSources();
  loadInitialQuotes();
  connectSSE();
})();
