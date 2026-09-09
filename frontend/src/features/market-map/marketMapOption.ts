import type { EChartsOption } from "echarts";
import type { MarketMapSector } from "@/types";
import { themeColors } from "@/utils/cssVar";
import { changeTileColor, type TileColors } from "@/utils/changeColor";

export type MapSizeMode = "equal" | "volume";

interface StockMeta {
  symbol: string;
  exchange: string;
  token: string | null;
  change: number | null;
  status: string;
  fno: boolean;
}

function volumeWeight(volume: number | null): number {
  if (!volume || volume <= 0) return 1;
  // Bounded log weight so a single high-volume stock cannot dominate the map.
  return Math.max(0.3, Math.min(3, (Math.log10(volume) - 3) / 2));
}

// Build a finviz-style treemap: each canonical sector is a group, each stock is
// a leaf. Size follows the backend/current-UI semantics (equal by default,
// bounded volume weight when selected). Color is the backend Change % only.
// Pure presentation over the canonical projection — no weighting/aggregation is
// computed here.
export function buildMarketMapOption(
  sectors: MarketMapSector[],
  sizeMode: MapSizeMode,
): EChartsOption {
  const c = themeColors();
  const colors: TileColors = {
    pos: c.pos,
    neg: c.neg,
    unavailable: c.textFaint,
  };
  const sectorFill = c.surface2;

  const ordered = [...sectors].sort((a, b) => {
    if (a.sector === "Unclassified") return 1;
    if (b.sector === "Unclassified") return -1;
    return a.sector.localeCompare(b.sector);
  });

  const data = ordered.map((sec) => {
    const isUnc = sec.sector === "Unclassified";
    const stocks = [...sec.stocks].sort(
      (a, b) => (b.change_percent ?? -Infinity) - (a.change_percent ?? -Infinity),
    );
    const children = stocks.map((st) => {
      const color =
        st.status === "unavailable"
          ? colors.unavailable
          : changeTileColor(st.change_percent, colors);
      const w = sizeMode === "volume" ? volumeWeight(st.volume) : 1;
      const meta: StockMeta = {
        symbol: st.symbol,
        exchange: st.exchange,
        token: st.instrument_token,
        change: st.change_percent,
        status: st.status,
        fno: st.fno,
      };
      return {
        name: st.symbol,
        value: [w, st.change_percent == null ? 0 : st.change_percent],
        itemStyle: { color },
        _meta: meta,
      };
    });
    return {
      name: `${sec.sector} (${stocks.length})`,
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
        const d = p as { data?: { _meta?: StockMeta } };
        const meta = d.data?._meta;
        if (meta) {
          const chg =
            meta.status === "unavailable" ? "no quote" : fmtChange(meta.change);
          return `<b>${meta.symbol}</b><br/>${chg}`;
        }
        return `<b>${(p as { name?: string }).name ?? ""}</b>`;
      },
    },
    series: [
      {
        type: "treemap",
        roam: true,
        nodeClick: "zoomToNode",
        breadcrumb: {
          show: true,
          top: 2,
          height: 20,
          itemStyle: { color: c.surface2, textStyle: { color: c.text } },
        },
        data,
        top: 26,
        left: 4,
        right: 4,
        bottom: 4,
        label: {
          show: true,
          color: c.text,
          fontSize: 11,
          formatter: (p: unknown) => {
            const d = p as { data?: { _meta?: StockMeta; name?: string } };
            const meta = d.data?._meta;
            if (meta) {
              const chg = meta.status === "unavailable" ? "N/A" : fmtChange(meta.change);
              return `${meta.symbol}\n${chg}`;
            }
            return d.data?.name ?? "";
          },
        },
        upperLabel: { show: true, height: 20, color: c.textMuted, fontSize: 12, fontWeight: 600 },
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
