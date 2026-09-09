import type { EChartsOption } from "echarts";
import type { ChainRowView } from "@/types";
import { themeColors } from "@/utils/cssVar";

// OI-by-strike bar chart (CE vs PE). Presentation only — OI values are taken
// verbatim from canonical quotes; nothing is aggregated/recomputed here beyond
// selecting the per-leg open_interest.
export function buildOiByStrikeOption(rows: ChainRowView[]): EChartsOption {
  const c = themeColors();
  const strikes = rows.map((r) => r.strike);
  const ce = rows.map((r) => r.call?.quote?.open_interest ?? null);
  const pe = rows.map((r) => r.put?.quote?.open_interest ?? null);

  return {
    animation: false,
    backgroundColor: "transparent",
    grid: { left: 60, right: 16, top: 28, bottom: 40 },
    legend: {
      data: ["CE OI", "PE OI"],
      top: 0,
      textStyle: { color: c.textMuted },
    },
    tooltip: {
      trigger: "axis",
      backgroundColor: c.surface,
      borderColor: c.border,
      textStyle: { color: c.text },
    },
    xAxis: {
      type: "category",
      data: strikes.map((s) => String(s)),
      axisLabel: { color: c.textMuted, rotate: 45 },
      axisLine: { lineStyle: { color: c.border } },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: c.textMuted },
      axisLine: { lineStyle: { color: c.border } },
      splitLine: { lineStyle: { color: c.border } },
    },
    series: [
      {
        name: "CE OI",
        type: "bar",
        data: ce,
        itemStyle: { color: c.pos },
      },
      {
        name: "PE OI",
        type: "bar",
        data: pe,
        itemStyle: { color: c.neg },
      },
    ],
  };
}
