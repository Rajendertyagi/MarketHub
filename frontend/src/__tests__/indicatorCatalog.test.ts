import { describe, expect, it, vi } from "vitest";

const vela = vi.hoisted(() => {
  const descriptor = (over: Record<string, unknown>) => ({
    multiInstance: false,
    beta: false,
    inputsSchema: () => [],
    defaultInputs: () => ({}),
    ...over,
  });
  const descriptors = [
    descriptor({
      type: "future-thing",
      title: "Future Thing",
      inputsSchema: () => [{ id: "period" }],
      defaultInputs: () => ({ period: 3 }),
    }),
    descriptor({ type: "vwap", title: "Volume Weighted Average Price" }),
    descriptor({
      type: "sma",
      title: "Simple Moving Average",
      shortTitle: "SMA",
      multiInstance: true,
      inputsSchema: () => [{ id: "length" }],
      defaultInputs: () => ({ length: 9 }),
    }),
    descriptor({ type: "volume", title: "Volume", multiInstance: true }),
    descriptor({ type: "rsi", title: "Relative Strength Index", shortTitle: "RSI" }),
  ];
  return { descriptors, nativeIndicatorDescriptors: vi.fn(() => descriptors) };
});

vi.mock("@luxalgo/vela", () => ({
  nativeIndicatorDescriptors: vela.nativeIndicatorDescriptors,
  registerWidgetAction: vi.fn(),
  registerWidgetAttachment: vi.fn(),
}));

import { buildIndicatorCatalog, INDICATOR_CATEGORY_ORDER } from "@/features/chart/indicatorCatalog";

describe("buildIndicatorCatalog", () => {
  const catalog = buildIndicatorCatalog();

  it("maps every native descriptor into a catalog entry", () => {
    expect(catalog).toHaveLength(vela.descriptors.length);
    expect(catalog.map((entry) => entry.type)).toContain("sma");
  });

  it("sorts by the declared category order", () => {
    const categories = catalog.map((entry) => entry.category);
    const ranks = categories.map((category) => INDICATOR_CATEGORY_ORDER.indexOf(category));
    expect(ranks).toEqual([...ranks].sort((a, b) => a - b));
    expect(catalog[0]?.type).toBe("sma");
    expect(catalog.at(-1)?.type).toBe("future-thing");
    expect(catalog.at(-1)?.category).toBe("Other");
  });

  it("keeps product order inside a category", () => {
    const volume = catalog.filter((entry) => entry.category === "Volume").map((e) => e.type);
    expect(volume).toEqual(["volume"]);
  });

  it("carries aliases, short titles and inputs from the native descriptor", () => {
    const sma = catalog.find((entry) => entry.type === "sma");
    expect(sma).toMatchObject({
      title: "Simple Moving Average",
      shortTitle: "SMA",
      multiInstance: true,
      inputs: [{ id: "length" }],
      defaults: { length: 9 },
    });
    expect(sma?.aliases).toContain("SMA");

    const vwap = catalog.find((entry) => entry.type === "vwap");
    expect(vwap?.aliases).toContain("volume weighted average price");
    expect(vwap?.chartOnly).toBe(true);
  });

  it("reads the registry lazily instead of at import time", () => {
    vela.nativeIndicatorDescriptors.mockImplementationOnce(() => []);
    expect(buildIndicatorCatalog()).toEqual([]);
  });
});
