/**
 * MarketHub WebUI — Sector Heatmap.
 *
 * Consumes GET /api/market/sector-heatmap, which aggregates per-sector metrics
 * using the SAME canonical universe + quote reader + single sector classifier as
 * Market Breadth (so sector totals reconcile with breadth totals). This module
 * only renders tiles and drill-down; no aggregation happens in the browser.
 *
 * Tile intensity is the EQUAL-WEIGHTED average constituent change % (the
 * endpoint's `weighting` is always "equal"). Unavailable / unclassified sectors
 * are shown distinctly (never as neutral performance).
 */

import { apiGet } from "./api.js";

const $ = (id) => document.getElementById(id);

let _bound = false;
let _timer = null;
let _current = null;
let _activeSector = null;
let _inflight = false; // guards against overlapping refresh requests

function _num(v, dash = "—") {
  if (v === null || v === undefined) return dash;
  return v;
}

function _fmtPct(v) {
  if (v === null || v === undefined) return "—";
  return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
}

// Map an equal-weighted average change % to a tile background color.
function _tileColor(avg) {
  if (avg === null || avg === undefined) return null; // caller uses .unclassified
  const cap = 5; // +/-5% saturates
  const mag = Math.max(-1, Math.min(1, avg / cap));
  if (mag >= 0) {
    // green, darker = stronger
    const l = 62 - mag * 32;
    return `hsl(140, 55%, ${l}%)`;
  }
  const l = 62 + mag * 32; // mag negative -> lighter red
  return `hsl(0, 60%, ${l}%)`;
}

async function loadHeatmap() {
  // Drop overlapping requests: rapid view switching must not start a second
  // in-flight fetch while one is already running (prevents request loops).
  if (_inflight) return;
  _inflight = true;
  const universe = ($("heatmap-universe")?.value) || "NIFTY50";
  try {
    const s = await apiGet(`/api/market/sector-heatmap?universe=${encodeURIComponent(universe)}&members=1`);
    if (s.error) {
      $("heatmap-message").textContent = s.error;
      return;
    }
    $("heatmap-message").textContent =
      `${s.universe} · ${s.sector_count} sectors · ` +
      `${s.classified_count} classified / ${s.unclassified_count} unclassified · ` +
      `weighting: ${s.weighting}`;
    const w = $("heatmap-weight");
    if (w) w.textContent = `${s.weighting}-weighted`;
    const fr = $("heatmap-freshness");
    if (fr) {
      fr.textContent = (s.stale ? "stale (last session) " : "as of ") +
        (s.as_of ? s.as_of.replace("T", " ").slice(0, 19) : "");
      fr.className = "chip " + (s.stale ? "chip-off" : "chip-on");
    }
    _current = s;
    _renderTiles(s);
    if (_activeSector) _renderDrill(s, _activeSector);
  } catch (e) {
    $("heatmap-message").textContent = `Heatmap load failed: ${e.message || e}`;
  } finally {
    _inflight = false;
  }
}

function _renderTiles(s) {
  const grid = $("heatmap-grid");
  grid.innerHTML = "";
  for (const sec of s.sectors) {
    const tile = document.createElement("div");
    tile.className = "heatmap-tile";
    const isUnc = sec.sector === "Unclassified";
    const color = isUnc ? null : _tileColor(sec.average_change_percent);
    if (color) tile.style.background = color;
    else tile.classList.add("unclassified");
    const meta = isUnc
      ? `${sec.constituent_count} stocks · no sector data`
      : `Adv ${sec.advances} / Dec ${sec.declines} · ${sec.quoted} quoted`;
    tile.innerHTML =
      `<div class="tile-sector">${sec.sector}</div>` +
      `<div class="tile-chg">${isUnc ? "—" : _fmtPct(sec.average_change_percent)}</div>` +
      `<div class="tile-meta">${meta}</div>`;
    if (sec.unavailable > 0 && !isUnc) {
      const w = document.createElement("div");
      w.className = "tile-warn";
      w.textContent = `${sec.unavailable} unavail`;
      tile.appendChild(w);
    }
    tile.addEventListener("click", () => {
      _activeSector = sec.sector;
      _renderDrill(s, sec.sector);
    });
    grid.appendChild(tile);
  }
}

function _renderDrill(s, sector) {
  const sec = s.sectors.find((x) => x.sector === sector);
  $("heatmap-drill-title").textContent = `Sector: ${sector}`;
  $("heatmap-drill-count").textContent =
    sec ? `${sec.constituent_count} constituents · ${sec.quoted} quoted · ` +
          `${sec.unavailable} unavailable` : "";
  const body = $("heatmap-body");
  if (!sec || !sec.members.length) {
    body.innerHTML = '<tr><td colspan="5" class="empty-row">No classified members in this sector.</td></tr>';
    return;
  }
  const rows = sec.members.slice().sort(
    (a, b) => (b.change_percent ?? -Infinity) - (a.change_percent ?? -Infinity));
  body.innerHTML = "";
  for (const m of rows) {
    const tr = document.createElement("tr");
    const chgCls = (m.change ?? 0) > 0 ? "up" : (m.change ?? 0) < 0 ? "down" : "";
    tr.innerHTML =
      `<td>${m.symbol}</td>` +
      `<td>${_num(m.ltp)}</td>` +
      `<td class="${chgCls}">${m.change === null ? "—" : (m.change >= 0 ? "+" : "") + m.change.toFixed(2)}</td>` +
      `<td class="${chgCls}">${_fmtPct(m.change_percent)}</td>` +
      `<td><span class="status-pill ${m.status}">${m.status}</span></td>`;
    body.appendChild(tr);
  }
}

function _startLive() {
  _stopLive();
  _timer = setInterval(() => {
    const active = $("view-sector-heatmap")?.classList.contains("active");
    if (!active) { _stopLive(); return; }
    loadHeatmap();
  }, 5000);
}

function _stopLive() {
  if (_timer) { clearInterval(_timer); _timer = null; }
}

export function initSectorHeatmapUI() {
  if (_bound) return;
  _bound = true;
  $("heatmap-universe")?.addEventListener("change", () => { _activeSector = null; loadHeatmap(); });
}

export async function openSectorHeatmap() {
  initSectorHeatmapUI();
  await loadHeatmap();
  _startLive();
}
