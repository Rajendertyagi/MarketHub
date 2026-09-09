/**
 * MarketHub WebUI — ECharts candlestick + volume charts.
 *
 * Owns the chart selection state and the single ECharts instance
 * (created lazily on first render, reused afterwards — never duplicated).
 */

import { $, escDash, fmt, fmtNum, fmtVol } from "./utils.js";
import { apiGet } from "./api.js";
import { switchView } from "./router.js";

let chartSelection = null;   // {instrument_key, exchange, tradingsymbol}
let chartInstance = null;    // singleton ECharts instance
let lastCandles = null;      // last rendered series, for live theme recolor
let _inited = false;         // guards initCharts against duplicate listeners

// Resolve a CSS custom property to a concrete color (handles color-mix too),
// so ECharts (canvas) can consume the active theme's tokens. The probe element
// is created lazily on first use so importing this module has NO DOM side
// effects (module load must stay side-effect safe).
let _colorProbe = null;

function _ensureProbe() {
  if (_colorProbe) return _colorProbe;
  _colorProbe = document.createElement("div");
  _colorProbe.style.display = "none";
  document.body.appendChild(_colorProbe);
  return _colorProbe;
}

function cssVar(name) {
  const probe = _ensureProbe();
  _colorProbe.style.color = `var(${name})`;
  const v = getComputedStyle(_colorProbe).color;
  if (v && v !== "rgba(0, 0, 0, 0)") return v;
  // Fallback to a MarketHub token, not a raw literal.
  _colorProbe.style.color = "var(--text-muted)";
  const f = getComputedStyle(_colorProbe).color;
  return f && f !== "rgba(0, 0, 0, 0)" ? f : "#888";
}

export function initCharts() {
  if (_inited) return;
  _inited = true;
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
            `<option value="${r.instrument_token}" data-ex="${escDash(r.exchange)}"` +
            ` data-sym="${escDash(r.tradingsymbol)}">` +
            `${escDash(r.tradingsymbol)} (${escDash(r.exchange)})</option>`).join("");
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

  $("chart-load").addEventListener("click", () => _loadChart());
  // Responsive resize — added exactly once (initCharts is idempotent) so rapid
  // navigation never accumulates duplicate listeners or ECharts instances.
  window.addEventListener("resize", _onResize);
  // Recolor the open chart live when the theme changes (no refetch needed).
  window.addEventListener("mh-themechange", () => {
    if (chartInstance && lastCandles) renderChart(lastCandles);
  });
}

function _onResize() {
  if (chartInstance) chartInstance.resize();
}

async function _loadChart() {
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
      const err = (d && d.error) || "History load failed.";
      msg.textContent = /unsupport|not (available|supported)/i.test(err)
        ? `Provider "${provider}" does not support history for this instrument.`
        : err;
      msg.className = "hint err";
      return;
    }
    if (!d.candles || !d.candles.length) {
      msg.textContent = "No history data returned for this range.";
      msg.className = "hint err";
      return;
    }
    const first = d.candles[0].timestamp.slice(0, 10);
    const last = d.candles[d.candles.length - 1].timestamp.slice(0, 10);
    msg.textContent = `${d.candles.length} candles · ${first} → ${last}`;
    msg.className = "hint ok";
    renderChart(d.candles);
  } catch {
    msg.textContent = "Network error loading history.";
    msg.className = "hint err";
  }
}

/**
 * Canonical navigation entry point. Accepts either a fully-resolved identity
 * (instrument_key present) or a symbol that is resolved via the catalog search
 * API. Resolves the EXACT instrument — never substitutes a future for an
 * equity or vice versa.
 */
export async function openChart(identity) {
  initCharts();
  let sel = null;
  if (identity && identity.instrument_key) {
    sel = {
      instrument_key: identity.instrument_key,
      exchange: identity.exchange,
      tradingsymbol: identity.tradingsymbol,
    };
  } else if (identity && identity.symbol) {
    const type = identity.type || "EQUITY";
    try {
      const d = await apiGet(
        `/api/instruments/search?limit=10&type=${encodeURIComponent(type)}` +
        `&q=${encodeURIComponent(identity.symbol)}`);
      const rs = (d && d.results || []).filter((r) => r.instrument_type === type);
      if (rs.length) {
        sel = {
          instrument_key: rs[0].instrument_token,
          exchange: rs[0].exchange,
          tradingsymbol: rs[0].tradingsymbol,
        };
      }
    } catch { /* fall through */ }
  }
  if (!sel) {
    const msg = $("chart-message");
    if (msg) msg.textContent = "Could not resolve instrument for chart.";
    return;
  }
  chartSelection = sel;
  const search = $("chart-search");
  if (search) search.value = sel.tradingsymbol;
  switchView("charts");
  _loadChart();
}

// Lifecycle: (re)initialize on enter, release the ECharts instance on leave so
// rapid navigation never leaks canvases or accumulates duplicate instances.
export function openCharts() {
  initCharts();
}

export function closeCharts() {
  disposeCharts();
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
  lastCandles = candles;
  const pos = cssVar("--pos");
  const neg = cssVar("--neg");
  const accent = cssVar("--accent");
  const info = cssVar("--info");
  const textMuted = cssVar("--text-muted");
  const border = cssVar("--border");
  const surface = cssVar("--surface-1");
  const textColor = cssVar("--text");
  const times = candles.map((c) =>
    c.timestamp.slice(0, 16).replace("T", " "));
  const closes = candles.map((c) => c.close);
  const opens = candles.map((c) => c.open);
  const kline = candles.map((c) => [c.open, c.close, c.low, c.high]);
  const vols = candles.map((c, i) => ({
    value: c.volume ?? 0,
    itemStyle: { color: closes[i] >= opens[i] ? pos : neg },
  }));
  chartInstance.setOption({
    animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "cross" },
      backgroundColor: surface, borderColor: border, textStyle: { color: textColor } },
    legend: { data: ["SMA20", "SMA50"], top: 0, textStyle: { color: textMuted } },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [{ left: 60, right: 20, top: 24, height: "56%" },
           { left: 60, right: 20, top: "72%", height: "18%" }],
    xAxis: [
      { type: "category", data: times,
        axisLabel: { color: textMuted },
        axisLine: { lineStyle: { color: border } },
        splitLine: { lineStyle: { color: border } } },
      { type: "category", gridIndex: 1, data: times,
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: border } },
        splitLine: { show: false } },
    ],
    yAxis: [
      { scale: true, axisLabel: { color: textMuted },
        axisLine: { lineStyle: { color: border } },
        splitLine: { lineStyle: { color: border } } },
      { gridIndex: 1, axisLabel: { show: false },
        axisLine: { lineStyle: { color: border } },
        splitLine: { show: false } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1] },
      { type: "slider", xAxisIndex: [0, 1], top: "92%",
        textStyle: { color: textMuted }, borderColor: border },
    ],
    series: [
      { type: "candlestick", name: "Price", data: kline,
        itemStyle: { color: pos, color0: neg, borderColor: pos, borderColor0: neg } },
      { type: "line", name: "SMA20", data: sma(closes, 20),
        showSymbol: false, lineStyle: { width: 1, color: accent } },
      { type: "line", name: "SMA50", data: sma(closes, 50),
        showSymbol: false, lineStyle: { width: 1, color: info } },
      { type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: vols },
    ],
  });
}

export function disposeCharts() {
  if (chartInstance && typeof chartInstance.dispose === "function") {
    try { chartInstance.dispose(); } catch { /* silent */ }
  }
  chartInstance = null;
}
