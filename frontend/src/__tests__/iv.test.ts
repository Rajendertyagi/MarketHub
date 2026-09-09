import { describe, expect, it } from "vitest";
import { ivToPercent } from "@/utils/iv";

describe("ivToPercent (canonical fraction -> percent, display only)", () => {
  it("converts 0.1758 to 17.58%", () => {
    expect(ivToPercent(0.1758)).toBe("17.58%");
  });

  it("handles 0 as valid", () => {
    expect(ivToPercent(0)).toBe("0.00%");
  });

  it("handles values > 1.0 as valid", () => {
    expect(ivToPercent(1.5)).toBe("150.00%");
  });

  it("returns - for null/undefined without heuristics", () => {
    expect(ivToPercent(null)).toBe("-");
    expect(ivToPercent(undefined)).toBe("-");
  });
});
