/**
 * MarketHub WebUI — ECharts candlestick + volume charts.
 *
 * Owns the chart selection state and the single ECharts instance
 * (created lazily on first render, reused afterwards — never duplicated).
 */

import { $, escDash, fmt, fmtNum, fmtVol } from "./utils.js";

let chartSelection = null;   // {instrument_key, exchange, tradingsymbol}
let chartInstance = null;    // singleton ECharts instance

export function initCharts() {
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

  $("chart-load").addEventListener("click", async () => {
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
        msg.textContent = d.error || "History load failed.";
        msg.className = "hint err";
        return;
      }
      if (!d.candles || !d.candles.length) {
        msg.textContent = "No history data returned for this range.";
        msg.className = "hint err";
        return;
      }
      msg.textContent = `${d.candles.length} candles loaded.`;
      msg.className = "hint ok";
      renderChart(d.candles);
    } catch {
      msg.textContent = "Network error loading history.";
      msg.className = "hint err";
    }
  });
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
  const times = candles.map((c) =>
    c.timestamp.slice(0, 16).replace("T", " "));
  const closes = candles.map((c) => c.close);
  const kline = candles.map((c) => [c.open, c.close, c.low, c.high]);
  const vols = candles.map((c) => c.volume ?? 0);
  chartInstance.setOption({
    animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    legend: { data: ["SMA20", "SMA50"], top: 0 },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [{ left: 60, right: 20, top: 24, height: "56%" },
           { left: 60, right: 20, top: "72%", height: "18%" }],
    xAxis: [
      { type: "category", data: times },
      { type: "category", gridIndex: 1, data: times,
        axisLabel: { show: false } },
    ],
    yAxis: [
      { scale: true },
      { gridIndex: 1, axisLabel: { show: false } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1] },
      { type: "slider", xAxisIndex: [0, 1], top: "92%" },
    ],
    series: [
      { type: "candlestick", name: "Price", data: kline,
        itemStyle: { color: "#3fb950", color0: "#f85149",
                     borderColor: "#3fb950", borderColor0: "#f85149" } },
      { type: "line", name: "SMA20", data: sma(closes, 20),
        showSymbol: false, lineStyle: { width: 1 } },
      { type: "line", name: "SMA50", data: sma(closes, 50),
        showSymbol: false, lineStyle: { width: 1 } },
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
