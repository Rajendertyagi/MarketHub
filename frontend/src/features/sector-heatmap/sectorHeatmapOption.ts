import type { EChartsOption } from "echarts";
import type { SectorHeatmapSnapshot } from "@/types";
import { themeColors } from "@/utils/cssVar";
import { changeTileColor, type TileColors } from "@/utils/changeColor";

interface LeafMeta {
  symbol: string;
  change: number | null;
  status: string;
  fno: boolean;
  ltp: number | null;
}

// Build a finviz-style treemap: each canonical sector is a group, each
// constituent is a leaf sized by equal area and colored by its Change % (or
// unavailable gray). Color encodes the backend change value only — no second
// semantic scale. Pure presentation over the canonical snapshot.
export function buildSectorHeatmapOption(s: SectorHeatmapSnapshot): EChartsOption {
  const c = themeColors();
  const colors: TileColors = {
    pos: c.pos,
    neg: c.neg,
    unavailable: c.textFaint,
  };
  const sectorFill = c.surface2;

  const data = s.sectors.map((sec) => {
    const isUnc = sec.sector === "Unclassified";
    const children = sec.members.map((m) => {
      const color =
        m.status === "unavailable"
          ? colors.unavailable
          : changeTileColor(m.change_percent, colors);
      const meta: LeafMeta = {
        symbol: m.symbol,
        change: m.change_percent,
        status: m.status,
        fno: m.fno,
        ltp: m.ltp,
      };
      return {
        name: m.symbol,
        value: [1, m.change_percent == null ? 0 : m.change_percent],
        itemStyle: { color },
        _meta: meta,
      };
    });
    return {
      name: sec.sector,
      itemStyle: {
        color: isUnc ? colors.unavailable : sectorFill,
        borderColor: c.border,
      },
      children,
    };
  });

  return {
    animation: false,
    backgroundColor: "transparent",
    tooltip: {
      backgroundColor: c.surface,
      borderColor: c.border,
      textStyle: { color: c.text },
      formatter: (p: unknown) => {
        const d = p as { data?: { _meta?: LeafMeta; name?: string } };
        const meta = d.data?._meta;
        if (meta) {
          const chg =
            meta.status === "unavailable" ? "no quote" : fmtChange(meta.change);
          return `<b>${meta.symbol}</b><br/>${chg}`;
        }
        return `<b>${d.data?.name ?? ""}</b>`;
      },
    },
    series: [
      {
        type: "treemap",
        roam: false,
        nodeClick: false,
        breadcrumb: { show: false },
        data,
        top: 4,
        left: 4,
        right: 4,
        bottom: 4,
        label: { show: true, color: c.text, fontSize: 11, formatter: "{b}" },
        upperLabel: { show: true, height: 20, color: c.textMuted, fontSize: 11 },
        itemStyle: { borderColor: c.surface, borderWidth: 1, gapWidth: 1 },
        levels: [
          { itemStyle: { borderWidth: 2, gapWidth: 2, borderColor: c.surface }, upperLabel: { show: true } },
          { itemStyle: { borderWidth: 1, gapWidth: 1, borderColorSaturation: 0.3 } },
        ],
      },
    ],
  };
}

function fmtChange(v: number | null): string {
  if (v === null || v === undefined) return "—";
  const cls = v > 0 ? "+" : "";
  return `${cls}${v.toFixed(2)}%`;
}
