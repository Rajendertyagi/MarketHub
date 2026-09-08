/**
 * MarketHub WebUI — Market Breadth.
 *
 * Consumes the canonical aggregated endpoint GET /api/market/breadth, which
 * already computes advances/declines/unavailable over the canonical quote store.
 * This module only renders; no breadth math happens in the browser (the single
 * source of truth is market/breadth.py on the backend).
 *
 * Live behaviour: on view open we fetch once, then re-fetch on a debounced
 * interval while the view is active (bounded — the endpoint is a cheap
 * server-side aggregate, not a per-symbol fan-out).
 */

import { apiGet } from "./api.js";
import { ensureAnalyticsCoverage, clearAnalyticsCoverage } from "./analytics.js";
import { onViewLeave } from "./router.js";

const $ = (id) => document.getElementById(id);

let _bound = false;
let _timer = null;
let _current = null; // last snapshot
let _inflight = false; // guards against overlapping refresh requests
let _coveredUniverse = null; // last universe we requested analytics coverage for

// Request cash-equity coverage only when the universe actually changes (covers
// first view-open and later switches); the 5s live refresh must not re-POST.
function _ensureCoverage(universe) {
  if (universe && universe !== _coveredUniverse) {
    _coveredUniverse = universe;
    ensureAnalyticsCoverage(universe);
  }
}

function _num(v, dash = "—") {
  if (v === null || v === undefined || (typeof v === "number" && Number.isNaN(v))) return dash;
  return v;
}

function _fmtPct(v) {
  if (v === null || v === undefined) return "—";
  return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
}

function _fmtChg(v) {
  if (v === null || v === undefined) return "—";
  return (v >= 0 ? "+" : "") + v.toFixed(2);
}

function _statusClass(status) {
  return status; // matches .status-pill.<status>
}

function _renderSummary(s) {
  const el = $("breadth-summary");
  const stat = (cls, label, value) =>
    `<div class="breadth-stat ${cls}"><span class="label">${label}</span>` +
    `<span class="value">${_num(value)}</span></div>`;
  el.innerHTML =
    stat("", "Advances", s.advances) +
    stat("", "Declines", s.declines) +
    stat("", "Unchanged", s.unchanged) +
    stat("", "Unavailable", s.unavailable) +
    stat("adv", "Advance %", _fmtPct(s.advance_percent)) +
    stat("dec", "Decline %", _fmtPct(s.decline_percent)) +
    stat("net", "Net Advances", _fmtChg(s.net_advances)) +
    stat("", "A/D Ratio", s.ad_ratio === null ? "∞" : s.ad_ratio.toFixed(2)) +
    stat("", "Eligible", s.eligible) +
    stat("", "Quoted", s.quoted);

  const fr = $("breadth-freshness");
  if (fr) {
    const label = s.stale ? "stale (last session)" : "as of";
    fr.textContent = `${label} ${s.as_of ? s.as_of.replace("T", " ").slice(0, 19) : ""}`;
    fr.className = "chip " + (s.stale ? "chip-off" : "chip-on");
  }
}

function _renderBar(s) {
  const el = $("breadth-bar");
  const total = s.eligible || 1;
  const pct = (n) => Math.max(0, (n / total) * 100);
  const seg = (cls, n, label) => {
    const w = pct(n);
    if (w <= 0) return `<span class="${cls} empty"></span>`;
    return `<span class="${cls}" style="width:${w}%">${label} ${n}</span>`;
  };
  el.innerHTML =
    seg("seg-adv", s.advances, "ADV") +
    seg("seg-dec", s.declines, "DEC") +
    seg("seg-unch", s.unchanged, "UNC") +
    seg("seg-unav", s.unavailable, "N/A");
}

function _renderTable(s) {
  const filter = ($("breadth-filter")?.value) || "all";
  const sortKey = ($("breadth-sort")?.value) || "symbol";
  let rows = s.rows.slice();
  if (filter !== "all") rows = rows.filter((r) => r.status === filter);
  rows.sort((a, b) => {
    if (sortKey === "symbol") return a.symbol.localeCompare(b.symbol);
    const av = a[sortKey] ?? -Infinity;
    const bv = b[sortKey] ?? -Infinity;
    return bv - av;
  });
  const body = $("breadth-body");
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty-row">No constituents match filter.</td></tr>';
    return;
  }
  body.innerHTML = "";
  for (const r of rows) {
    const tr = document.createElement("tr");
    const chgCls = (r.change ?? 0) > 0 ? "up" : (r.change ?? 0) < 0 ? "down" : "";
    tr.innerHTML =
      `<td>${r.symbol}</td>` +
      `<td>${_num(r.ltp)}</td>` +
      `<td class="${chgCls}">${_fmtChg(r.change)}</td>` +
      `<td class="${chgCls}">${_fmtPct(r.change_percent)}</td>` +
      `<td>${r.sector}</td>` +
      `<td><span class="status-pill ${_statusClass(r.status)}">${r.status}</span></td>`;
    body.appendChild(tr);
  }
}

async function loadBreadth() {
  // Drop overlapping requests: rapid view switching must not start a second
  // in-flight fetch while one is already running (prevents request loops).
  if (_inflight) return;
  _inflight = true;
  const universe = ($("breadth-universe")?.value) || "NIFTY50";
  _ensureCoverage(universe);
  try {
    const s = await apiGet(`/api/market/breadth?universe=${encodeURIComponent(universe)}`);
    if (s.error) {
      $("breadth-message").textContent = s.error;
      return;
    }
    $("breadth-message").textContent =
      `${s.universe} · ${s.unclassified} unclassified sector` +
      (s.unclassified === 1 ? "" : "s");
    _current = s;
    _renderSummary(s);
    _renderBar(s);
    _renderTable(s);
  } catch (e) {
    $("breadth-message").textContent = `Breadth load failed: ${e.message || e}`;
  } finally {
    _inflight = false;
  }
}

function _startLive() {
  _stopLive();
  _timer = setInterval(() => {
    const active = $("view-breadth")?.classList.contains("active");
    if (!active) { _stopLive(); return; }
    loadBreadth();
  }, 5000);
}

function _stopLive() {
  if (_timer) { clearInterval(_timer); _timer = null; }
}

export function initBreadthUI() {
  if (_bound) return;
  _bound = true;
  $("breadth-universe")?.addEventListener("change", loadBreadth);
  $("breadth-filter")?.addEventListener("change", () => _current && _renderTable(_current));
  $("breadth-sort")?.addEventListener("change", () => _current && _renderTable(_current));
  onViewLeave("breadth", () => { clearAnalyticsCoverage(); _coveredUniverse = null; });
}

export async function openBreadth() {
  initBreadthUI();
  if (!$("breadth-body") || $("breadth-body").dataset.loaded === "1") {
    // Already loaded once; just (re)start live refresh.
  }
  await loadBreadth();
  $("breadth-body").dataset.loaded = "1";
  _startLive();
}
