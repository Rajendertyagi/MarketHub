// Regression test for the shared React API URL builder (closes b05fa4c class).
//
// Proves that feature paths are prefixed with /api and that already-correct
// /api paths are left untouched, with query params preserved and same-origin
// (pathname + search only) output for production.

import { describe, expect, it } from "vitest";
import { buildIdlessUrl } from "@/api/client";

describe("buildIdlessUrl (API prefix regression)", () => {
  it("prefixes a feature path with /api", () => {
    expect(buildIdlessUrl("/market/breadth")).toBe("/api/market/breadth");
  });

  it("does NOT double-prefix an already-correct /api path", () => {
    expect(buildIdlessUrl("/api/market/breadth")).toBe(
      "/api/market/breadth",
    );
  });

  it("keeps a deeper feature path under /api", () => {
    expect(buildIdlessUrl("/options/chain/view")).toBe(
      "/api/options/chain/view",
    );
  });

  it("appends query parameters", () => {
    const url = buildIdlessUrl("/market/breadth", { universe: "NIFTY" });
    expect(url).toBe("/api/market/breadth?universe=NIFTY");
  });

  it("omits empty/undefined params but keeps real ones", () => {
    const url = buildIdlessUrl("/options/expiries", {
      underlying: "NIFTY",
      expiry: "",
      window: undefined,
    });
    expect(url).toBe("/api/options/expiries?underlying=NIFTY");
  });

  it("preserves multiple query parameters", () => {
    const url = buildIdlessUrl("/options/chain/view", {
      underlying: "NIFTY",
      expiry: "2026-10-30",
      window: 10,
    });
    expect(url).toBe(
      "/api/options/chain/view?underlying=NIFTY&expiry=2026-10-30&window=10",
    );
  });

  it("returns a same-origin relative path (no host) for production", () => {
    const url = buildIdlessUrl("/market/fno/view", { symbol: "HDFCBANK" });
    expect(url.startsWith("http")).toBe(false);
    expect(url).toBe("/api/market/fno/view?symbol=HDFCBANK");
  });
});
