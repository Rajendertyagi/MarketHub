import { describe, expect, it } from "vitest";
import { askOf, bidOf, normalizeChainRows, normalizeEquityOptions } from "@/features/fno/useFno";
import type { ChainRow, FnoOption } from "@/types";
import { fmtIv } from "@/utils/format";

describe("fmtIv (canonical IV is a decimal fraction)", () => {
  it("formats a fraction as a percentage", () => {
    expect(fmtIv(0.1758)).toBe("17.58%");
    expect(fmtIv(1.5)).toBe("150.00%");
  });
  it("keeps 0 as 0% and null as unavailable", () => {
    expect(fmtIv(0)).toBe("0.00%");
    expect(fmtIv(null)).toBe("-");
    expect(fmtIv(undefined)).toBe("-");
  });
});

describe("quote bid/ask normalization (defensive, never fabricated)", () => {
  it("prefers bid/ask, falls back to best_bid/best_ask", () => {
    expect(bidOf({ bid: 10 })).toBe(10);
    expect(bidOf({ best_bid: 11 })).toBe(11);
    expect(askOf({ ask: 20 })).toBe(20);
    expect(askOf({ best_ask: 21 })).toBe(21);
    expect(bidOf(undefined)).toBeUndefined();
    expect(askOf(null)).toBeUndefined();
  });
});

describe("option row normalizers", () => {
  it("groups equity options by strike into CE/PE legs", () => {
    const opts: FnoOption[] = [
      {
        key: "k1",
        label: "X2500CE",
        expiry: "e",
        strike: 2500,
        option_type: "CE",
        provider: "p",
        quote: { ltp: 50 },
      },
      {
        key: "k2",
        label: "X2500PE",
        expiry: "e",
        strike: 2500,
        option_type: "PE",
        provider: "p",
        quote: { ltp: 40 },
      },
      {
        key: "k3",
        label: "X2600CE",
        expiry: "e",
        strike: 2600,
        option_type: "CE",
        provider: "p",
        quote: { ltp: 30 },
      },
    ];
    const rows = normalizeEquityOptions(opts, 2500);
    expect(rows).toHaveLength(2);
    const r0 = rows[0];
    const r1 = rows[1];
    if (!r0 || !r1) throw new Error("expected two rows");
    expect(r0.strike).toBe(2500);
    expect(r0.atm).toBe(true);
    expect(r0.call?.label).toBe("X2500CE");
    expect(r0.put?.label).toBe("X2500PE");
    expect(r1.atm).toBe(false);
    expect(r1.call?.label).toBe("X2600CE");
  });

  it("normalizes index chain rows into the shared view model", () => {
    const rows: ChainRow[] = [
      {
        strike: 25000,
        atm: true,
        call: {
          instrument_key: "NSE:1",
          symbol: "NIFTY25000CE",
          exchange: "NSE",
          option_type: "CE",
          strike: 25000,
          quote: { ltp: 100 },
        },
        put: {
          instrument_key: "NSE:2",
          symbol: "NIFTY25000PE",
          exchange: "NSE",
          option_type: "PE",
          strike: 25000,
          quote: { ltp: 90 },
        },
      },
    ];
    const view = normalizeChainRows(rows);
    expect(view).toHaveLength(1);
    const v0 = view[0];
    if (!v0) throw new Error("expected one view row");
    expect(v0.call?.key).toBe("NSE:1");
    expect(v0.put?.key).toBe("NSE:2");
    expect(v0.atm).toBe(true);
  });
});
