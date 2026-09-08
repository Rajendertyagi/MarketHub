/**
 * MarketHub WebUI — Sector Heatmap.
 *
 * Consumes GET /api/market/sector-heatmap, which aggregates per-sector metrics
 * using the SAME canonical universe + quote reader + single sector classifier as
 * Market Breadth (so sector totals reconcile with breadth totals). This module
 * only renders; no aggregation happens in the browser.
 *
 * Visualization: a finviz-style ECharts TREEMAP — each canonical sector is a
 * group, each constituent stock is a leaf sized by volume (or equal when volume
 * is unavailable) and colored by its Change % using the SAME continuous scale as
 * the previous heatmap tiles (so color semantics agree with Breadth/Market Map).
 * Unavailable stocks get a distinct gray tile (never faked to neutral). Clicking
 * a stock reuses the existing F&O workspace (switchView + openStock); non-
 * derivative stocks are declined honestly. If ECharts is not present, the module
 * falls back to the plain CSS sector grid so the feature never breaks.
 *
 * Refresh: single debounced timer + in-flight guard, self-clearing when the view
 * is inactive (identical proven strategy as Breadth/Market Map).
 */

import { apiGet } from "./api.js";
import { switchView } from "./router.js";
import { openStock } from "./fno.js";

const $ = (id) => document.getElementById(id);

let _bound = false;
let _timer = null;
let _current = null;
let _activeSector = null;
let _inflight = false;       // guards against overlapping refresh requests
let _chart = null;           // ECharts instance (lazy)

function _num(v, dash = "—") {
  if (v === null || v === undefined) return dash;
  return v;
}

function _fmtPct(v) {
  if (v === null || v === undefined) return "—";
  return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
}

// Continuous Change% -> tile color (green up / red down, saturates at +/-5%).
// Mirrors the canonical movement semantics; matches the prior heatmap tiles.
function _tileColor(avg) {
  if (avg === null || avg === undefined) return null; // caller uses gray
  const cap = 5;
  const mag = Math.max(-1, Math.min(1, avg / cap));
  if (mag >= 0) {
    const l = 62 - mag * 32;
    return `hsl(140, 55%, ${l}%)`;
  }
  const l = 62 + mag * 32;
  return `hsl(0, 60%, ${l}%)`;
}

function _theme() {
  const dark = document.documentElement.getAttribute("data-theme") !== "light";
  return {
    dark,
    label: dark ? "#e6edf3" : "#1b1f27",
    upper: dark ? "#c9d4e0" : "#3a4150",
    gap: dark ? "#0d1117" : "#ffffff",
    border: dark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.07)",
    unavailable: dark ? "#2a2f3a" : "#c9ced6",
    sectorFill: dark ? "#161a22" : "#eef1f5",
  };
}

async function loadHeatmap() {
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
    _render(s);
    if (_activeSector) _renderDrill(s, _activeSector);
  } catch (e) {
    $("heatmap-message").textContent = `Heatmap load failed: ${e.message || e}`;
  } finally {
    _inflight = false;
  }
}

// Primary: ECharts treemap. Fallback: plain CSS sector grid.
function _render(s) {
  if (window.echarts) {
    const grid = $("heatmap-grid");
    const chart = $("heatmap-chart");
    if (grid) grid.style.display = "none";
    if (chart) chart.style.display = "block";
    _renderTreemap(s);
  } else {
    const chart = $("heatmap-chart");
    const grid = $("heatmap-grid");
    if (chart) chart.style.display = "none";
    if (grid) grid.style.display = "flex";
    _renderGridFallback(s);
  }
}

function _buildOption(s) {
  const t = _theme();
  const data = s.sectors.map((sec) => {
    const isUnc = sec.sector === "Unclassified";
    const children = (sec.members || []).map((m) => {
      const color = m.status === "unavailable" ? t.unavailable : _tileColor(m.change_percent);
      return {
        name: m.symbol,
        // Equal area per stock; Change % is encoded by COLOR (the primary signal).
        // Avoids a single high-volume stock swallowing the whole map when volume
        // data is sparse/inconsistent.
        value: [1, m.change_percent == null ? 0 : m.change_percent],
        itemStyle: { color },
        _symbol: m.symbol,
        _fno: m.fno,
        _change: m.change_percent,
        _status: m.status,
      };
    });
    return {
      name: sec.sector,
      itemStyle: {
        color: isUnc ? t.unavailable : t.sectorFill,
        borderColor: t.border,
      },
      children,
    };
  });

  return {
    backgroundColor: "transparent",
    tooltip: {
      backgroundColor: t.dark ? "#0d1117" : "#ffffff",
      borderColor: t.border,
      textStyle: { color: t.label },
      formatter: (p) => {
        const d = p.data || {};
        if (d._symbol) {
          const chg = d._status === "unavailable" ? "no quote" : _fmtPct(d._change);
          return `<b>${d._symbol}</b><br/>${chg}`;
        }
        return `<b>${p.name}</b>`;
      },
    },
    series: [{
      type: "treemap",
      roam: false,
      nodeClick: false,
      breadcrumb: { show: false },
      data,
      top: 4, left: 4, right: 4, bottom: 4,
      label: { show: true, color: t.label, fontSize: 11, formatter: "{b}" },
      upperLabel: { show: true, height: 20, color: t.upper, fontSize: 11 },
      itemStyle: { borderColor: t.gap, borderWidth: 1, gapWidth: 1 },
      levels: [
        { itemStyle: { borderWidth: 2, gapWidth: 2, borderColor: t.gap }, upperLabel: { show: true } },
        { itemStyle: { borderWidth: 1, gapWidth: 1, borderColorSaturation: 0.3 } },
      ],
    }],
  };
}

function _ensureChart() {
  const el = $("heatmap-chart");
  if (!el) return null;
  if (!_chart && window.echarts) {
    _chart = window.echarts.init(el, null, { renderer: "canvas" });
    _chart.on("click", (params) => {
      const d = params.data || {};
      if (d._symbol) {
        if (d._fno) {
          switchView("fno");
          openStock(d._symbol);
        } else {
          const msg = $("heatmap-message");
          if (msg) msg.textContent =
            `${d._symbol} has no listed derivatives — open the Instruments page to inspect it.`;
        }
      } else if (params.name) {
        _activeSector = params.name;
        if (_current) _renderDrill(_current, params.name);
      }
    });
  }
  return _chart;
}

function _renderTreemap(s) {
  const chart = _ensureChart();
  if (!chart) return;
  chart.setOption(_buildOption(s), true);
  // Container may have just become visible; size on next frame.
  requestAnimationFrame(() => chart.resize());
}

// Fallback renderer (no ECharts): one tile per sector, drill on click.
function _renderGridFallback(s) {
  const grid = $("heatmap-grid");
  if (!grid) return;
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
    tr.style.cursor = m.fno ? "pointer" : "default";
    if (m.fno) {
      tr.addEventListener("click", () => { switchView("fno"); openStock(m.symbol); });
    }
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
  window.addEventListener("resize", () => { if (_chart) _chart.resize(); });
}

export async function openSectorHeatmap() {
  initSectorHeatmapUI();
  await loadHeatmap();
  _startLive();
}
