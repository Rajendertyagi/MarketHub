import type { EChartsOption } from "echarts";
import type { BreadthSnapshot } from "@/types";
import { themeColors } from "@/utils/cssVar";

// Build a single horizontal stacked bar showing the breadth split:
// advances / declines / unchanged / unavailable. Pure presentation over the
// canonical snapshot — no breadth math happens here (the backend already
// computed advances/declines/...).
export function buildBreadthBarOption(s: BreadthSnapshot): EChartsOption {
  const c = themeColors();
  const total = s.eligible || 1;
  const pct = (n: number) => `${((n / total) * 100).toFixed(1)}%`;

  return {
    animation: false,
    backgroundColor: "transparent",
    grid: { left: 90, right: 16, top: 10, bottom: 24 },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: c.surface,
      borderColor: c.border,
      textStyle: { color: c.text },
      formatter: (params: unknown) => {
        const arr = params as Array<{ name: string; value: number; seriesName: string }>;
        if (!arr.length) return "";
        const p = arr[0]!;
        return `${p.seriesName}: <b>${p.value}</b> (${pct(p.value)})`;
      },
    },
    xAxis: {
      type: "value",
      axisLabel: { color: c.textMuted },
      axisLine: { lineStyle: { color: c.border } },
      splitLine: { lineStyle: { color: c.border } },
    },
    yAxis: {
      type: "category",
      data: ["Breadth"],
      axisLabel: { color: c.textMuted },
      axisLine: { lineStyle: { color: c.border } },
    },
    series: [
      {
        name: "Advances",
        type: "bar",
        stack: "b",
        data: [s.advances],
        itemStyle: { color: c.pos },
        label: { show: true, color: c.text, formatter: () => (s.advances ? String(s.advances) : "") },
      },
      {
        name: "Declines",
        type: "bar",
        stack: "b",
        data: [s.declines],
        itemStyle: { color: c.neg },
        label: { show: true, color: c.text, formatter: () => (s.declines ? String(s.declines) : "") },
      },
      {
        name: "Unchanged",
        type: "bar",
        stack: "b",
        data: [s.unchanged],
        itemStyle: { color: c.textMuted },
        label: { show: true, color: c.text, formatter: () => (s.unchanged ? String(s.unchanged) : "") },
      },
      {
        name: "Unavailable",
        type: "bar",
        stack: "b",
        data: [s.unavailable],
        itemStyle: { color: c.border },
        label: { show: true, color: c.text, formatter: () => (s.unavailable ? String(s.unavailable) : "") },
      },
    ],
  };
}
