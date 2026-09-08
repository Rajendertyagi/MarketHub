/**
 * MarketHub WebUI — F&O Stocks: automatic universe + stock workspace.
 *
 * The universe is DERIVED from the synced instrument catalog (no manual
 * add workflow): clicking a stock opens the workspace, which loads a
 * snapshot-first payload (spot/futures/option chain) and establishes a
 * bounded ACTIVE-VIEW subscription (equity + futures + ATM ± window)
 * without restart or relogin. Switching stocks reconciles the view;
 * persistent subscriptions are never touched from here.
 */

import { apiGet, apiPost } from "../api.js";

const $ = (id) => document.getElementById(id);

let _bound = false;
let _universe = [];
let _currentSymbol = null;

function _msg(text, isError = false) {
  const el = $("fno-message");
  if (!el) return;
  el.textContent = text || "";
  el.classList.toggle("err", !!isError);
}

function _num(v, dash = "—") {
  return (v === null || v === undefined) ? dash : String(v);
}

async function loadUniverse(q = "") {
  try {
    const data = await apiGet(
      "/api/market/fno/universe?limit=500" + (q ? `&q=${encodeURIComponent(q)}` : ""));
    _universe = data.universe || [];
    $("fno-count").textContent = `${data.count} stocks`;
    const body = $("fno-body");
    body.innerHTML = "";
    if (!_universe.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty-row">'
        + "No F&O stocks match. Sync the Upstox instrument master first."
        + "</td></tr>";
      return;
    }
    for (const s of _universe) {
      const tr = document.createElement("tr");
      tr.className = "fno-row";
      const addCell = (v) => {
        const td = document.createElement("td");
        td.textContent = _num(v);
        return td;
      };
      tr.appendChild(addCell(s.symbol));
      tr.appendChild(addCell(s.name));
      tr.appendChild(addCell(s.futures_available ? `✓ ${s.futures_count}` : "—"));
      tr.appendChild(addCell(s.options_available ? `✓ ${s.options_count}` : "—"));
      tr.appendChild(addCell(s.nearest_future));
      tr.appendChild(addCell(s.nearest_option));
      const tdOpen = document.createElement("td");
      const btn = document.createElement("button");
      btn.className = "btn btn-compact";
      btn.textContent = "Open";
      btn.addEventListener("click", () => openStock(s.symbol));
      tdOpen.appendChild(btn);
      tr.appendChild(tdOpen);
      body.appendChild(tr);
    }
  } catch (e) {
    _msg(`Universe load failed: ${e.message || e}`, true);
  }
}

function _renderSpot(q) {
  const el = $("fno-ws-spot");
  if (!el) return;
  if (!q || q.ltp === null || q.ltp === undefined) {
    el.innerHTML = '<span class="hint">Spot: no current quote '
      + '(market closed or not subscribed yet)</span>';
    return;
  }
  const chg = q.change ?? null;
  const pct = q.change_percent ?? null;
  el.innerHTML =
    `<span class="fno-ltp">₹${q.ltp}</span>`
    + `<span class="fno-chg ${(chg ?? 0) >= 0 ? "up" : "down"}">`
    + `${chg === null ? "—" : chg.toFixed(2)} `
    + `(${pct === null ? "—" : pct.toFixed(2) + "%"})</span>`
    + `<span class="hint">O ${_num(q.open)} H ${_num(q.high)} `
    + `L ${_num(q.low)} · Vol ${_num(q.volume)} · bid/ask `
    + `${_num(q.best_bid)}/${_num(q.best_ask)}</span>`;
}

function _renderFutures(futures) {
  const body = $("fno-fut-body");
  body.innerHTML = "";
  if (!futures.length) {
    body.innerHTML = '<tr><td colspan="9" class="empty-row">'
      + "No non-expired futures in catalog</td></tr>";
    return;
  }
  for (const f of futures) {
    const tr = document.createElement("tr");
    const q = f.quote || {};
    const cell = (v) => {
      const td = document.createElement("td");
      td.textContent = _num(v);
      return td;
    };
    tr.appendChild(cell(f.expiry));
    tr.appendChild(cell(f.label));
    tr.appendChild(cell(q.ltp));
    tr.appendChild(cell(q.change));
    tr.appendChild(cell(q.change_percent));
    tr.appendChild(cell(q.volume));
    tr.appendChild(cell(q.best_bid));
    tr.appendChild(cell(q.best_ask));
    tr.appendChild(cell(q.received_ts));
    body.appendChild(tr);
  }
}

function _renderChain(options, atm) {
  const body = $("fno-opt-body");
  body.innerHTML = "";
  $("fno-atm").textContent = atm ? `ATM: ${atm}` : "ATM: —";
  if (!options.length) {
    body.innerHTML = '<tr><td colspan="13" class="empty-row">'
      + "No option contracts (spot unavailable for ATM resolution?)"
      + "</td></tr>";
    return;
  }
  const byStrike = new Map();
  for (const o of options) {
    const slot = byStrike.get(o.strike) || {};
    slot[o.option_type] = o;
    byStrike.set(o.strike, slot);
  }
  for (const [strike, slot] of [...byStrike.entries()].sort((a, b) => a[0] - b[0])) {
    const tr = document.createElement("tr");
    tr.className = strike === atm ? "fno-atm-row" : undefined;
    const cell = (v) => {
      const td = document.createElement("td");
      td.textContent = _num(v);
      return td;
    };
    const ce = slot.CE || {}, pe = slot.PE || {};
    const qc = ce.quote || {}, qp = pe.quote || {};
    tr.append(
      cell(qc.open_interest), cell(qc.oi_change), cell(qc.volume),
      cell(qc.iv), cell(qc.ltp), cell(qc.best_bid !== undefined
        ? `${_num(qc.best_bid)}/${_num(qc.best_ask)}` : null),
      cell(strike),
      cell(qp.ltp), cell(qp.best_bid !== undefined
        ? `${_num(qp.best_bid)}/${_num(qp.best_ask)}` : null),
      cell(qp.iv), cell(qp.volume), cell(qp.oi_change),
      cell(qp.open_interest));
    body.appendChild(tr);
  }
}

export async function openStock(symbol) {
  try {
    _currentSymbol = symbol;
    $("fno-universe-panel").classList.add("hidden");
    $("fno-workspace").classList.remove("hidden");
    $("fno-ws-title").textContent = symbol;
    $("fno-ws-status").textContent = "loading…";
    $("fno-ws-note").textContent = "";
    // Snapshot first: render whatever canonical state exists now.
    const ws = await apiGet(
      `/api/market/fno/stock/${encodeURIComponent(symbol)}?window=10`);
    _renderSpot(ws.spot_quote);
    _renderFutures(ws.futures || []);
    const sel = $("fno-expiry-select");
    sel.innerHTML = "";
    for (const e of ws.option_expiries || []) {
      const opt = document.createElement("option");
      opt.value = e;
      opt.textContent = e;
      sel.appendChild(opt);
    }
    if (ws.selected_expiry) sel.value = ws.selected_expiry;
    _renderChain(ws.options || [], ws.atm);
    const n = ws.notes || [];
    $("fno-ws-note").textContent = n.length ? n.join("; ") : "";
    // Then establish the bounded active-view subscription (no restart).
    try {
      const applied = await apiPost("/api/market/fno/view", {
        symbol, window: 10, future_count: 2, expiry_count: 1,
      });
      const parts = Object.entries(applied.apply?.results || {})
        .map(([p, r]) => `${p}: ${r.applied ? "applied" : r.reason}`);
      $("fno-ws-status").textContent
        = `live: ${Object.values(applied.active_view || {}).join("/")}`
        + ` (${parts.join(", ")})`;
    } catch (e) {
      $("fno-ws-status").textContent = "live subscriptions pending";
    }
  } catch (e) {
    _msg(`Workspace failed: ${e.message || e}`, true);
  }
}

function _backToUniverse() {
  _currentSymbol = null;
  $("fno-workspace").classList.add("hidden");
  $("fno-universe-panel").classList.remove("hidden");
  // Active-view cleanup happens on the NEXT view apply; leaving the page
  // keeps the current workspace subscriptions until another view/stock
  // replaces them (no unsubscribe race while the user may return).
}

export function initFnoUI() {
  if (_bound) return;
  _bound = true;
  $("fno-search")?.addEventListener("input", (ev) => {
    loadUniverse(ev.target.value.trim());
  });
  $("fno-ws-close")?.addEventListener("click", _backToUniverse);
  $("fno-expiry-select")?.addEventListener("change", () => {
    if (_currentSymbol) openStock(_currentSymbol);
  });
}

export async function openFno() {
  initFnoUI();
  if (!$("fno-workspace").classList.contains("hidden")) return;
  await loadUniverse($("fno-search")?.value.trim() || "");
}
