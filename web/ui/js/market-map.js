/**
 * MarketHub WebUI — Market Map.
 *
 * Stock-level, sector-grouped whole-market visual built on the EXISTING
 * canonical foundation: the same /api/market/map backend projection that reuses
 * the Breadth/Sector-Heatmap universe resolver + sector classifier + canonical
 * quote reader. This module only renders; no aggregation happens in the browser.
 *
 * Visualization: a finviz-style ECharts TREEMAP — each canonical sector is a
 * group, each constituent stock is a leaf sized by equal area (or a bounded
 * volume weight) and colored by ITS OWN Change % using the SAME continuous scale
 * as the Sector Heatmap/Breadth (so color semantics agree across features).
 * Unavailable stocks get a distinct gray tile (never faked to neutral). Clicking
 * a stock reuses the existing F&O workspace (switchView + openStock); non-
 * derivative stocks are declined honestly. Clicking a sector drills in (zoom).
 * If ECharts is not present, the module falls back to the plain CSS sector grid
 * so the feature never breaks.
 *
 * Refresh: identical proven strategy as Breadth/Heatmap — a single debounced
 * timer, an in-flight guard (no overlapping requests), self-clearing when the
 * view is inactive, so rapid navigation cannot accumulate timers or loops.
 */

import { apiGet } from "./api.js";
import { switchView } from "./router.js";
import { openStock } from "./fno.js";

const $ = (id) => document.getElementById(id);

let _bound = false;
let _timer = null;
let _current = null;       // last snapshot
let _inflight = false;     // guards against overlapping refresh requests
let _chart = null;         // ECharts instance (lazy)
const MAX_TILES = 800;     // bounded render for very large universes (NSE_EQ)

function _num(v, dash = "—") {
  if (v === null || v === undefined || (typeof v === "number" && Number.isNaN(v))) return dash;
  return v;
}

function _fmtPct(v) {
  if (v === null || v === undefined) return "—";
  return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
}

// Continuous Change% -> tile color (green up / red down, saturates at +/-5%).
// Mirrors the canonical movement semantics; matches the Sector Heatmap tiles.
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

// Symmetric, explicit CSS color scale (fallback grid only).
function _colorClass(status, pct) {
  if (status === "unavailable") return "mm-unavailable";
  if (pct == null) return "mm-flat";
  if (pct >= 2) return "mm-strong-up";
  if (pct >= 0.5) return "mm-up";
  if (pct > -0.5) return "mm-flat";
  if (pct > -2) return "mm-down";
  return "mm-strong-down";
}

function _onStockClick(symbol, fno) {
  if (fno) {
    switchView("fno");
    openStock(symbol);
  } else {
    const msg = $("mm-message");
    if (msg) msg.textContent =
      `${symbol} has no listed derivatives — open the Instruments page to inspect it.`;
  }
}

// Apply the Sector / Movement / Search filters and group by sector.
function _filteredStocks(snap) {
  const sectorFilter = ($("mm-sector")?.value) || "";
  const movement = ($("mm-movement")?.value) || "all";
  const q = (($("mm-search")?.value) || "").trim().toUpperCase();
  let all = [];
  for (const sg of snap.sectors) for (const st of sg.stocks) all.push(st);
  const filtered = all.filter((st) => {
    if (sectorFilter && st.sector !== sectorFilter) return false;
    if (movement !== "all" && st.status !== movement) return false;
    if (q && !st.symbol.toUpperCase().includes(q)) return false;
    return true;
  });
  const total = filtered.length;
  const truncated = total > MAX_TILES;
  const shown = truncated ? filtered.slice(0, MAX_TILES) : filtered;
  const bySector = new Map();
  for (const st of shown) {
    if (!bySector.has(st.sector)) bySector.set(st.sector, []);
    bySector.get(st.sector).push(st);
  }
  return { bySector, truncated, total };
}

function _sectorOrder(keys) {
  return [...keys].sort((a, b) => {
    if (a === "Unclassified") return 1;
    if (b === "Unclassified") return -1;
    return a.localeCompare(b);
  });
}

function _renderSummary(snap) {
  const el = $("mm-summary");
  if (!el) return;
  const chip = (cls, label, value) =>
    `<div class="breadth-stat ${cls}"><span class="label">${label}</span>` +
    `<span class="value">${_num(value)}</span></div>`;
  el.innerHTML =
    chip("", "Eligible", snap.eligible) +
    chip("", "Quoted", snap.quoted) +
    chip("", "Unavailable", snap.unavailable) +
    chip("adv", "Advances", snap.advances) +
    chip("dec", "Declines", snap.declines) +
    chip("", "Unchanged", snap.unchanged) +
    chip("", "Sectors", snap.sectors.length) +
    chip("", "Unclassified", snap.unclassified);
  const fr = $("mm-freshness");
  if (fr) {
    const label = snap.stale ? "stale (last session)" : "as of";
    fr.textContent = `${label} ${snap.as_of ? snap.as_of.replace("T", " ").slice(0, 19) : ""}`;
    fr.className = "chip " + (snap.stale ? "chip-off" : "chip-on");
  }
}

// ── Primary: ECharts treemap ────────────────────────────────────────────────
function _buildOption(filtered) {
  const t = _theme();
  const sizeMode = ($("mm-size")?.value) || "equal";
  const ordered = _sectorOrder(filtered.bySector.keys());
  const data = ordered.map((sec) => {
    const stocks = filtered.bySector.get(sec).slice().sort(
      (a, b) => (b.change_percent ?? -Infinity) - (a.change_percent ?? -Infinity));
    const children = stocks.map((st) => {
      const color = st.status === "unavailable" ? t.unavailable : _tileColor(st.change_percent);
      let w = 1;
      if (sizeMode === "volume" && st.volume && st.volume > 0) {
        // Bounded log weight so a single high-volume stock cannot dominate.
        w = Math.max(0.3, Math.min(3, (Math.log10(st.volume) - 3) / 2));
      }
      return {
        name: st.symbol,
        // Equal area per stock; Change % is encoded by COLOR (primary signal).
        value: [w, st.change_percent == null ? 0 : st.change_percent],
        itemStyle: { color },
        _symbol: st.symbol,
        _fno: st.fno,
        _change: st.change_percent,
        _status: st.status,
      };
    });
    const isUnc = sec === "Unclassified";
    return {
      name: `${sec} (${stocks.length})`,
      itemStyle: { color: isUnc ? t.unavailable : t.sectorFill, borderColor: t.border },
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
      roam: true,
      nodeClick: "zoomToNode",          // click a sector to drill in
      breadcrumb: {
        show: true, top: 2, height: 20,
        itemStyle: { color: t.dark ? "#1b2230" : "#dfe5ec", textStyle: { color: t.label } },
      },
      data,
      top: 26, left: 4, right: 4, bottom: 4,
      label: {
        show: true, color: t.label, fontSize: 11,
        formatter: (p) => {
          const d = p.data || {};
          if (d._symbol) {
            const chg = d._status === "unavailable" ? "N/A" : _fmtPct(d._change);
            return `${d._symbol}\n${chg}`;
          }
          return p.name;
        },
      },
      upperLabel: { show: true, height: 20, color: t.upper, fontSize: 12, fontWeight: 600 },
      itemStyle: { borderColor: t.gap, borderWidth: 1, gapWidth: 1 },
      levels: [
        { itemStyle: { borderWidth: 2, gapWidth: 2, borderColor: t.gap }, upperLabel: { show: true } },
        { itemStyle: { borderWidth: 1, gapWidth: 1, borderColorSaturation: 0.3 } },
      ],
    }],
  };
}

function _ensureChart() {
  const el = $("mm-chart");
  if (!el) return null;
  if (!_chart && window.echarts) {
    _chart = window.echarts.init(el, null, { renderer: "canvas" });
    _chart.on("click", (params) => {
      const d = params.data || {};
      if (!d._symbol) return; // sector node -> handled by nodeClick zoom
      _onStockClick(d._symbol, d._fno);
    });
  }
  return _chart;
}

function _renderTreemap(filtered) {
  const chart = _ensureChart();
  if (!chart) return;
  chart.setOption(_buildOption(filtered), true);
  // Container may have just become visible; size on next frame.
  requestAnimationFrame(() => chart.resize());
}

// ── Fallback: plain CSS sector grid (no ECharts) ───────────────────────────
function _tile(stock, sizeMode) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "mm-tile " + _colorClass(stock.status, stock.change_percent);
  btn.dataset.symbol = stock.symbol;
  const pct = stock.change_percent;
  const chgTxt = stock.status === "unavailable" ? "N/A" : _fmtPct(pct);
  btn.setAttribute(
    "aria-label",
    `${stock.symbol}, sector ${stock.sector}, change ${chgTxt}, ${stock.status}`,
  );
  if (sizeMode === "volume" && stock.volume && stock.volume > 0) {
    const w = Math.max(96, Math.min(220, 96 + Math.log10(stock.volume) * 22));
    btn.style.flex = `0 0 ${w}px`;
  }
  const sym = document.createElement("span");
  sym.className = "mm-sym";
  sym.textContent = stock.symbol;
  const chg = document.createElement("span");
  chg.className = "mm-chg";
  chg.textContent = chgTxt;
  btn.appendChild(sym);
  btn.appendChild(chg);
  btn.addEventListener("click", () => _onStockClick(stock.symbol, stock.fno));
  return btn;
}

function _renderGrid(filtered) {
  const map = $("mm-map");
  if (!map) return;
  const sizeMode = ($("mm-size")?.value) || "equal";
  map.innerHTML = "";
  const ordered = _sectorOrder(filtered.bySector.keys());
  for (const sec of ordered) {
    const wrap = document.createElement("div");
    wrap.className = "mm-sector";
    const title = document.createElement("div");
    title.className = "mm-sector-title";
    title.textContent = `${sec} (${filtered.bySector.get(sec).length})`;
    wrap.appendChild(title);
    const tiles = document.createElement("div");
    tiles.className = "mm-tiles";
    for (const st of filtered.bySector.get(sec)) tiles.appendChild(_tile(st, sizeMode));
    wrap.appendChild(tiles);
    map.appendChild(wrap);
  }
}

// ── Dispatcher ──────────────────────────────────────────────────────────────
function _render(snap) {
  const filtered = _filteredStocks(snap);
  if (window.echarts) {
    const grid = $("mm-map");
    const chart = $("mm-chart");
    if (grid) grid.style.display = "none";
    if (chart) chart.style.display = "block";
    _renderTreemap(filtered);
  } else {
    const grid = $("mm-map");
    const chart = $("mm-chart");
    if (chart) chart.style.display = "none";
    if (grid) grid.style.display = "block";
    _renderGrid(filtered);
  }
  const note = $("mm-note");
  if (note) {
    note.textContent = filtered.truncated
      ? `Showing ${MAX_TILES} of ${filtered.total} matching stocks — refine Sector/Movement/Search for a denser view.`
      : "";
  }
}

function _populateSectors(snap) {
  const sel = $("mm-sector");
  if (!sel || sel.dataset.populated) return;
  for (const sg of snap.sectors) {
    if (sg.sector === "Unclassified") continue;
    const opt = document.createElement("option");
    opt.value = sg.sector;
    opt.textContent = sg.sector;
    sel.appendChild(opt);
  }
  sel.dataset.populated = "1";
}

async function loadMap() {
  if (_inflight) return;
  _inflight = true;
  const universe = ($("mm-universe")?.value) || "FNO";
  try {
    const s = await apiGet(`/api/market/map?universe=${encodeURIComponent(universe)}`);
    if (s.error) {
      $("mm-message").textContent = s.error;
      return;
    }
    $("mm-message").textContent =
      `${s.universe} · ${s.sectors.length} sectors · ${s.unclassified} unclassified`;
    _populateSectors(s);
    _current = s;
    _renderSummary(s);
    _render(s);
  } catch (e) {
    $("mm-message").textContent = `Market Map load failed: ${e.message || e}`;
  } finally {
    _inflight = false;
  }
}

function _startLive() {
  _stopLive();
  _timer = setInterval(() => {
    const active = $("view-market-map")?.classList.contains("active");
    if (!active) { _stopLive(); return; }
    loadMap();
  }, 5000);
}

function _stopLive() {
  if (_timer) { clearInterval(_timer); _timer = null; }
}

export function initMarketMapUI() {
  if (_bound) return;
  _bound = true;
  $("mm-universe")?.addEventListener("change", () => {
    const sel = $("mm-sector");
    if (sel) { sel.dataset.populated = ""; sel.innerHTML = '<option value="">All Sectors</option>'; }
    loadMap();
  });
  $("mm-sector")?.addEventListener("change", () => _current && _render(_current));
  $("mm-movement")?.addEventListener("change", () => _current && _render(_current));
  $("mm-search")?.addEventListener("input", () => _current && _render(_current));
  $("mm-size")?.addEventListener("change", () => _current && _render(_current));
  window.addEventListener("resize", () => { if (_chart) _chart.resize(); });
}

export async function openMarketMap() {
  initMarketMapUI();
  await loadMap();
  _startLive();
}
