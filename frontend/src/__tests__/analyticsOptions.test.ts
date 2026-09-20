import type { EChartsOption } from "echarts";
import { describe, expect, it } from "vitest";
import { buildBreadthBarOption } from "@/features/breadth/breadthOption";
import { buildMarketMapOption } from "@/features/market-map/marketMapOption";
import { buildSectorHeatmapOption } from "@/features/sector-heatmap/sectorHeatmapOption";
import type { BreadthSnapshot, MarketMapSnapshot, SectorHeatmapSnapshot } from "@/types";
import { changeTileColor } from "@/utils/changeColor";
import { themeColors } from "@/utils/cssVar";

const COLORS = { pos: "#0a0", neg: "#a00", unavailable: "#888" };

describe("changeTileColor (presentation-only scale)", () => {
  it("returns unavailable gray for null change", () => {
    expect(changeTileColor(null, COLORS)).toBe(COLORS.unavailable);
    expect(changeTileColor(undefined, COLORS)).toBe(COLORS.unavailable);
  });

  it("saturates at +/-5% and maps to green/red hsl", () => {
    expect(changeTileColor(5, COLORS)).toBe("hsl(140, 55%, 30%)");
    expect(changeTileColor(-5, COLORS)).toBe("hsl(0, 60%, 30%)");
    expect(changeTileColor(0, COLORS)).toBe("hsl(140, 55%, 62%)");
    // Beyond cap, color does not keep intensifying.
    expect(changeTileColor(50, COLORS)).toBe("hsl(140, 55%, 30%)");
  });
});

describe("buildBreadthBarOption", () => {
  const snap: BreadthSnapshot = {
    universe: "NIFTY50",
    eligible: 10,
    quoted: 8,
    unavailable: 2,
    advances: 5,
    declines: 2,
    unchanged: 1,
    advance_percent: 50,
    decline_percent: 20,
    ad_ratio: 2.5,
    net_advances: 3,
    as_of: "2024-01-01T00:00:00Z",
    unclassified: 0,
    stale: false,
    volume_advancing: null,
    volume_declining: null,
    intraday_highs: null,
    intraday_lows: null,
    weighting: "equal",
    rows: [],
  };

  it("produces a 4-segment stacked bar from canonical counts", () => {
    const opt = buildBreadthBarOption(snap) as unknown as {
      series: Array<{ name: string; type: string; data: number[] }>;
    };
    expect(opt.series).toHaveLength(4);
    const byName: Record<string, number> = {};
    for (const s of opt.series) {
      const v = s.data[0];
      if (v === undefined) throw new Error(`no data for series ${s.name}`);
      byName[s.name] = v;
    }
    expect(byName.Advances).toBe(5);
    expect(byName.Declines).toBe(2);
    expect(byName.Unchanged).toBe(1);
    expect(byName.Unavailable).toBe(2);
    opt.series.forEach((s) => {
      expect(s.type).toBe("bar");
    });
  });
});

describe("buildSectorHeatmapOption", () => {
  const snap: SectorHeatmapSnapshot = {
    universe: "NIFTY50",
    sector_count: 2,
    classified_count: 1,
    unclassified_count: 1,
    eligible: 2,
    quoted: 1,
    unavailable: 1,
    advances: 1,
    declines: 0,
    unchanged: 0,
    as_of: null,
    weighting: "equal",
    stale: false,
    sectors: [
      {
        sector: "ENERGY",
        constituent_count: 1,
        quoted: 1,
        unavailable: 0,
        advances: 1,
        declines: 0,
        unchanged: 0,
        average_change_percent: 0.4,
        median_change_percent: 0.4,
        net_advances: 1,
        top_gainer: null,
        top_loser: null,
        weighting: "equal",
        members: [
          {
            symbol: "RELIANCE",
            name: null,
            ltp: 2500,
            change: 10,
            change_percent: 0.4,
            volume: 1000,
            status: "advance",
            fno: true,
          },
        ],
      },
      {
        sector: "Unclassified",
        constituent_count: 1,
        quoted: 0,
        unavailable: 1,
        advances: 0,
        declines: 0,
        unchanged: 0,
        average_change_percent: null,
        median_change_percent: null,
        net_advances: 0,
        top_gainer: null,
        top_loser: null,
        weighting: "equal",
        members: [
          {
            symbol: "X",
            name: null,
            ltp: null,
            change: null,
            change_percent: null,
            volume: null,
            status: "unavailable",
            fno: false,
          },
        ],
      },
    ],
    reconciliation: {},
  };

  it("builds a treemap with one group per sector and preserves Unclassified", () => {
    const tc = themeColors();
    const opt = buildSectorHeatmapOption(snap) as unknown as {
      series: Array<{ type: string; data: Array<{ name: string; children: unknown[] }> }>;
    };
    const s0 = opt.series[0];
    if (!s0) throw new Error("expected one series");
    expect(s0.type).toBe("treemap");
    const groups = s0.data;
    expect(groups.map((g) => g.name)).toContain("Unclassified");
    expect(groups).toHaveLength(2);
    // The unavailable member is colored with the unavailable token (never neutral green).
    const uncGroup = groups.find((g) => g.name === "Unclassified");
    if (!uncGroup) throw new Error("missing Unclassified group");
    const leaf = uncGroup.children[0] as { itemStyle: { color: string } } | undefined;
    if (!leaf) throw new Error("missing Unclassified leaf");
    expect(leaf.itemStyle.color).toBe(tc.textFaint);
  });
});

describe("buildMarketMapOption", () => {
  const snap: MarketMapSnapshot = {
    universe: "FNO",
    eligible: 2,
    quoted: 1,
    unavailable: 1,
    advances: 1,
    declines: 0,
    unchanged: 0,
    unclassified: 0,
    as_of: null,
    stale: false,
    sectors: [
      {
        sector: "ENERGY",
        stocks: [
          {
            symbol: "RELIANCE",
            exchange: "NSE",
            instrument_token: "1",
            sector: "ENERGY",
            ltp: 2500,
            change: 10,
            change_percent: 0.4,
            volume: 5_000_000,
            status: "advance",
            fno: true,
            received_ts: null,
          },
        ],
        advances: 1,
        declines: 0,
        unchanged: 0,
        unavailable: 0,
        quoted: 1,
      },
      {
        sector: "Unclassified",
        stocks: [
          {
            symbol: "X",
            exchange: "NSE",
            instrument_token: null,
            sector: "Unclassified",
            ltp: null,
            change: null,
            change_percent: null,
            volume: null,
            status: "unavailable",
            fno: false,
            received_ts: null,
          },
        ],
        advances: 0,
        declines: 0,
        unchanged: 0,
        unavailable: 1,
        quoted: 0,
      },
    ],
    reconciliation: {},
  };

  function leaves(opt: EChartsOption) {
    const series = (opt as unknown as { series: Array<{ data: Array<{ children?: unknown[] }> }> })
      .series;
    const s0 = series[0];
    if (!s0) throw new Error("expected one series");
    return s0.data.flatMap(
      (g) =>
        (g.children ?? []) as Array<{
          value: number[];
          itemStyle: { color: string };
          _meta: { symbol: string };
        }>,
    );
  }

  function leafValue(l: { value: number[] }): number {
    const v = l.value[0];
    if (v === undefined) throw new Error("leaf missing value");
    return v;
  }

  it("uses equal area (weight 1) in equal mode", () => {
    const opt = buildMarketMapOption(snap.sectors, "equal");
    leaves(opt).forEach((l) => {
      expect(leafValue(l)).toBe(1);
    });
  });

  it("uses a bounded volume weight in volume mode", () => {
    const opt = buildMarketMapOption(snap.sectors, "volume");
    const rel = leaves(opt).find((l) => l._meta.symbol === "RELIANCE");
    if (!rel) throw new Error("missing RELIANCE leaf");
    expect(leafValue(rel)).toBeGreaterThan(1);
    expect(leafValue(rel)).toBeLessThanOrEqual(3);
  });

  it("colors unavailable tiles with the unavailable token", () => {
    const tc = themeColors();
    const opt = buildMarketMapOption(snap.sectors, "equal");
    const x = leaves(opt).find((l) => l._meta.symbol === "X");
    if (!x) throw new Error("missing X leaf");
    expect(x.itemStyle.color).toBe(tc.textFaint);
  });
});
