import type { EChartsOption } from "echarts";
import type { Candle } from "@/types";
import { themeColors } from "@/utils/cssVar";

function sma(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = [];
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i]!;
    if (i >= period) sum -= values[i - period]!;
    out.push(i >= period - 1 ? +(sum / period).toFixed(4) : null);
  }
  return out;
}

// Build a candlestick + volume ECharts option from canonical candles.
// Colors are resolved from CSS theme tokens at call time so theme switches
// recolor the chart. SMA20/50 are presentation-only overlays preserved from
// the legacy frontend.
export function buildChartOption(candles: Candle[]): EChartsOption {
  const c = themeColors();
  const times = candles.map((k) => k.timestamp.slice(0, 16).replace("T", " "));
  const closes = candles.map((k) => k.close);
  const opens = candles.map((k) => k.open);
  const kline = candles.map((k) => [k.open, k.close, k.low, k.high]);
  const vols = candles.map((k, i) => ({
    value: k.volume ?? 0,
    itemStyle: { color: closes[i]! >= opens[i]! ? c.pos : c.neg },
  }));

  return {
    animation: false,
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      backgroundColor: c.surface,
      borderColor: c.border,
      textStyle: { color: c.text },
    },
    legend: { data: ["SMA20", "SMA50"], top: 0, textStyle: { color: c.textMuted } },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    grid: [
      { left: 60, right: 20, top: 28, height: "56%" },
      { left: 60, right: 20, top: "72%", height: "18%" },
    ],
    xAxis: [
      {
        type: "category",
        data: times,
        axisLabel: { color: c.textMuted },
        axisLine: { lineStyle: { color: c.border } },
        splitLine: { lineStyle: { color: c.border } },
      },
      {
        type: "category",
        gridIndex: 1,
        data: times,
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: c.border } },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      {
        scale: true,
        axisLabel: { color: c.textMuted },
        axisLine: { lineStyle: { color: c.border } },
        splitLine: { lineStyle: { color: c.border } },
      },
      {
        gridIndex: 1,
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: c.border } },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1] },
      {
        type: "slider",
        xAxisIndex: [0, 1],
        top: "92%",
        textStyle: { color: c.textMuted },
        borderColor: c.border,
      },
    ],
    series: [
      {
        type: "candlestick",
        name: "Price",
        data: kline,
        itemStyle: {
          color: c.pos,
          color0: c.neg,
          borderColor: c.pos,
          borderColor0: c.neg,
        },
      },
      {
        type: "line",
        name: "SMA20",
        data: sma(closes, 20),
        showSymbol: false,
        lineStyle: { width: 1, color: c.accent },
      },
      {
        type: "line",
        name: "SMA50",
        data: sma(closes, 50),
        showSymbol: false,
        lineStyle: { width: 1, color: c.info },
      },
      { type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: vols },
    ],
  };
}
