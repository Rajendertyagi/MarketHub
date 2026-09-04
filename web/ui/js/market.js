/**
 * MarketHub WebUI — live market data core.
 *
 * Owns the subscribed-quote universe, the single market SSE stream
 * (/api/market/stream, opened once at boot, never duplicated), and all
 * in-place market rendering: dashboard/markets tables, ticker strip,
 * market cards, movers, inferred market status, markets filter, and the
 * initial snapshot fetch.
 */

import {
  $, chgClass, escDash, fmt, fmtNum, fmtTs, fmtVol, nowStr, setIndicator,
} from "./utils.js";

export const quotes = new Map();   // composite_key → quote data object
let es = null;                     // market EventSource (singleton)
const dashRows = new Map();        // key → <tr> element (in-place update)
const mktRows = new Map();

export function getQuote(key) {
  return quotes.get(key);
}

// ── SSE connection ──────────────────────────────────────────────────────

export function connectSSE() {
  if (es) return;   // singleton: boot calls this exactly once
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

export function handleQuoteUpdate(data) {
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
    row.classList.add("cursor-pointer");
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

// ── Subscribed-universe movers + inferred market status ─────────────────

export function renderMovers() {
  const body = $("movers-body");
  if (!body || quotes.size === 0) return;
  const all = [...quotes.values()].filter((q) => q.change_percent != null);
  if (!all.length) return;
  const byPct = [...all].sort((a, b) => b.change_percent - a.change_percent);
  const byVol = [...all].sort((a, b) => (b.volume ?? 0) - (a.volume ?? 0));
  const rows = [];
  const addRow = (cat, q) => rows.push(
    `<tr><td>${cat}</td><td>${escDash(q.tradingsymbol) || "—"}</td>` +
    `<td>${q.ltp != null ? fmt(q.ltp) : "—"}</td>` +
    `<td class="${chgClass(q.change ?? 0)}">${fmt(q.change_percent)}%</td>` +
    `<td>${fmtVol(q.volume)}</td></tr>`);
  byPct.slice(0, 2).forEach((q) => addRow("Top Gainer", q));
  byPct.slice(-2).reverse().forEach((q) => { if (q.change_percent < 0) addRow("Top Loser", q); });
  byVol.slice(0, 2).forEach((q) => addRow("Volume Leader", q));
  body.innerHTML = rows.join("") ||
    '<tr><td colspan="5" class="empty-row">No data yet</td></tr>';
}

export function renderMarketStatus() {
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

// ── Market filter (Markets page) ────────────────────────────────────────

export function initFilter() {
  $("market-filter").addEventListener("input", (e) => {
    const term = e.target.value.toLowerCase();
    mktRows.forEach((row, key) => {
      const sym = row.querySelector(".sym").textContent.toLowerCase();
      row.classList.toggle("hidden", !sym.includes(term));
    });
  });
}

// ── Initial snapshot fetch ──────────────────────────────────────────────

export async function loadInitialQuotes() {
  try {
    const res = await fetch("/api/market/quotes");
    const data = await res.json();
    if (data.quotes) for (const q of data.quotes) handleQuoteUpdate(q);
  } catch { /* SSE will populate */ }
}
