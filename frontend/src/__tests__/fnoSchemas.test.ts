import { describe, expect, it } from "vitest";
import {
  fnoUniverseSchema,
  fnoWorkspaceSchema,
  optionChainViewSchema,
  optionExpiriesSchema,
  optionUnderlyingsSchema,
  futuresResponseSchema,
} from "@/api/schemas";

describe("F&O contract schemas", () => {
  it("accepts a well-formed F&O universe response", () => {
    const res = fnoUniverseSchema.safeParse({
      status: "ok",
      count: 1,
      universe: [
        {
          symbol: "HDFCBANK",
          name: "HDFC Bank",
          equity_key: "NSE:123",
          futures_available: true,
          options_available: true,
          futures_count: 2,
          options_count: 10,
          future_expiries: 3,
          option_expiries: 3,
          nearest_future: "2026-10",
          nearest_option: "2026-10",
        },
      ],
    });
    expect(res.success).toBe(true);
  });

  it("accepts a well-formed equity F&O workspace", () => {
    const res = fnoWorkspaceSchema.safeParse({
      status: "ok",
      symbol: "HDFCBANK",
      equity_key: "NSE:123",
      spot_quote: { ltp: 2500, change: 10, change_percent: 0.4 },
      futures: [
        {
          key: "NSE_FO:1",
          label: "HDFCBANK26OCTFUT",
          expiry: "2026-10-29",
          provider: "upstox",
          quote: { ltp: 2505, best_bid: 2504, best_ask: 2506 },
        },
      ],
      options: [
        {
          key: "NSE_FO:2",
          label: "HDFCBANK26OCT2500CE",
          expiry: "2026-10-29",
          strike: 2500,
          option_type: "CE",
          provider: "upstox",
          quote: { ltp: 50, bid: 49, ask: 51, iv: 0.1758, delta: 0.5 },
        },
      ],
      option_expiries: ["2026-10-29"],
      selected_expiry: "2026-10-29",
      atm: 2500,
      notes: ["ok"],
    });
    expect(res.success).toBe(true);
  });

  it("rejects a workspace with a bad option_type", () => {
    const res = fnoWorkspaceSchema.safeParse({
      status: "ok",
      symbol: "X",
      equity_key: null,
      futures: [],
      options: [
        {
          key: "k",
          label: "l",
          expiry: "e",
          strike: 1,
          option_type: "XX",
          provider: null,
        },
      ],
      option_expiries: [],
      selected_expiry: null,
      atm: null,
    });
    expect(res.success).toBe(false);
  });

  it("accepts a well-formed index option-chain view", () => {
    const res = optionChainViewSchema.safeParse({
      underlying: "NIFTY",
      expiry: "2026-10-30",
      expiries_available: ["2026-10-30", "2026-11-27"],
      spot: 25000,
      spot_basis: "live",
      atm_strike: 25000,
      window: 10,
      strikes_loaded: 1,
      strikes_total_listed: 50,
      rows: [
        {
          strike: 25000,
          atm: true,
          call: {
            instrument_key: "NSE:1",
            symbol: "NIFTY25000CE",
            exchange: "NSE",
            option_type: "CE",
            strike: 25000,
            quote: { ltp: 100, iv: 0.18, delta: 0.5 },
          },
          put: {
            instrument_key: "NSE:2",
            symbol: "NIFTY25000PE",
            exchange: "NSE",
            option_type: "PE",
            strike: 25000,
            quote: { ltp: 90, iv: 0.19, delta: -0.5 },
          },
        },
      ],
      analytics: {
        scope: "loaded_window",
        pcr_by_oi: 0.9,
        total_call_oi: 1000,
        total_put_oi: 1100,
      },
    });
    expect(res.success).toBe(true);
  });

  it("accepts option underlyings and expiries", () => {
    expect(
      optionUnderlyingsSchema.safeParse({ underlyings: ["NIFTY", "BANKNIFTY"] })
        .success,
    ).toBe(true);
    expect(
      optionExpiriesSchema.safeParse({
        underlying: "NIFTY",
        expiries: ["2026-10-30"],
      }).success,
    ).toBe(true);
  });

  it("accepts a futures-by-underlying response", () => {
    const res = futuresResponseSchema.safeParse({
      underlying: "NIFTY",
      expiries: ["2026-10-30"],
      contracts: [
        {
          instrument_key: "NSE:1",
          symbol: "NIFTY26OCTFUT",
          exchange: "NSE",
          expiry: "2026-10-30",
          type: "FUTURE",
        },
      ],
    });
    expect(res.success).toBe(true);
  });
});
