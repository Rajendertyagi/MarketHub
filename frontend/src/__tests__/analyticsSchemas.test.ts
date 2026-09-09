import { describe, expect, it } from "vitest";
import {
  breadthSchema,
  marketMapSchema,
  sectorHeatmapSchema,
} from "@/api/schemas";

// Representative responses shaped exactly like the backend *.to_dict() outputs.
// These assert the frontend contract matches the canonical services — if the
// backend changes its shape, these tests fail loudly (parse error) instead of
// silently dropping fields.

const breadth = {
  universe: "NIFTY50",
  eligible: 3,
  quoted: 2,
  unavailable: 1,
  advances: 1,
  declines: 1,
  unchanged: 0,
  advance_percent: 50,
  decline_percent: 50,
  ad_ratio: 1,
  net_advances: 0,
  as_of: "2024-01-01T00:00:00Z",
  unclassified: 0,
  stale: false,
  volume_advancing: null,
  volume_declining: null,
  intraday_highs: null,
  intraday_lows: null,
  weighting: "equal",
  rows: [
    {
      symbol: "RELIANCE",
      name: null,
      exchange: "NSE",
      instrument_token: "1",
      ltp: 2500,
      change: 10,
      change_percent: 0.4,
      status: "advance",
      sector: "ENERGY",
      received_ts: null,
    },
  ],
};

const heatmap = {
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
  reconciliation: { advances_match: true },
};

const map = {
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
          volume: 1000,
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
  reconciliation: { advances_match: true },
};

describe("analytics API contracts (Zod)", () => {
  it("validates the breadth snapshot shape", () => {
    const r = breadthSchema.safeParse(breadth);
    expect(r.success).toBe(true);
    if (r.success) {
      expect(r.data.advances).toBe(1);
      expect(r.data.rows[0]!.symbol).toBe("RELIANCE");
    }
  });

  it("rejects a malformed breadth snapshot", () => {
    const r = breadthSchema.safeParse({ ...breadth, advances: "oops" });
    expect(r.success).toBe(false);
  });

  it("validates the sector heatmap snapshot shape", () => {
    const r = sectorHeatmapSchema.safeParse(heatmap);
    expect(r.success).toBe(true);
    if (r.success) {
      expect(r.data.sectors.map((s) => s.sector)).toContain("Unclassified");
      expect(r.data.sectors[0]!.members[0]!.fno).toBe(true);
    }
  });

  it("validates the market map snapshot shape", () => {
    const r = marketMapSchema.safeParse(map);
    expect(r.success).toBe(true);
    if (r.success) {
      const all = r.data.sectors.flatMap((s) => s.stocks);
      expect(all.find((s) => s.symbol === "RELIANCE")?.instrument_token).toBe("1");
    }
  });
});
