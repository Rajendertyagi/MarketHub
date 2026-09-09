import { describe, expect, it } from "vitest";
import { normalizeCandles, isChronological } from "@/features/charts/candleUtils";
import type { Candle } from "@/types";

function c(ts: string, close: number): Candle {
  return { timestamp: ts, open: close, high: close, low: close, close };
}

describe("normalizeCandles", () => {
  it("sorts newest-first input into chronological (oldest -> newest) order", () => {
    const input = [
      c("2024-01-03T00:00:00Z", 3),
      c("2024-01-01T00:00:00Z", 1),
      c("2024-01-02T00:00:00Z", 2),
    ];
    const out = normalizeCandles(input);
    expect(out.map((k) => k.close)).toEqual([1, 2, 3]);
  });

  it("is idempotent for already-ascending input", () => {
    const input = [c("2024-01-01T00:00:00Z", 1), c("2024-01-02T00:00:00Z", 2)];
    const out = normalizeCandles(input);
    expect(out.map((k) => k.close)).toEqual([1, 2]);
    expect(isChronological(out)).toBe(true);
  });

  it("does not mutate the canonical input array", () => {
    const input = [c("2024-01-02T00:00:00Z", 2), c("2024-01-01T00:00:00Z", 1)];
    const snapshot = [...input];
    normalizeCandles(input);
    expect(input).toEqual(snapshot);
  });

  it("handles empty input", () => {
    expect(normalizeCandles([])).toEqual([]);
  });
});
