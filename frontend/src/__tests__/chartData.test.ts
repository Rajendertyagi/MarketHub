import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/market", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/market")>();
  return {
    ...actual,
    getMarketBars: vi.fn(),
    resolveMarketInstrument: vi.fn(),
  };
});

import { getMarketBars, resolveMarketInstrument } from "@/api/market";
import {
  CHART_TIMEFRAMES,
  chartSymbol,
  MarketHubDataProvider,
  tickerFor,
} from "@/features/chart/chartData";
import type { Instrument, MarketInstrument } from "@/types";

const RELIANCE: Instrument = {
  instrument_token: "NSE_EQ|INE002A01018",
  exchange: "NSE",
  tradingsymbol: "RELIANCE",
  instrument_type: "EQUITY",
  provider: "upstox",
};

const INFY_ROW: MarketInstrument = {
  instrument_key: "NSE:NSE:INFY-EQ",
  provider: "fyers",
  provider_token: "NSE:INFY-EQ",
  symbol: "INFY",
  exchange: "NSE",
  type: "EQUITY",
};

const mockBars = getMarketBars as ReturnType<typeof vi.fn>;
const mockResolve = resolveMarketInstrument as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Vela symbol identity", () => {
  it("keeps the provider prefix and the exchange-qualified ticker", () => {
    expect(tickerFor(RELIANCE)).toBe("NSE:RELIANCE");
    expect(chartSymbol(RELIANCE)).toBe("markethub:NSE:RELIANCE");
  });
});

describe("MarketHubDataProvider", () => {
  it("serves persisted bars as ascending, deduplicated OHLCV", async () => {
    mockBars.mockResolvedValue({
      provider: "upstox",
      instrument_key: RELIANCE.instrument_token,
      timeframe: 15,
      bars: [
        {
          ts: 1_700_000_000_000,
          open: 101,
          high: 103,
          low: 100,
          close: 102,
          volume: null,
          oi: 7,
          source: "rest",
        },
        {
          ts: 1_699_999_000_000,
          open: 99,
          high: 102,
          low: 98,
          close: 101,
          volume: 500,
          oi: null,
          source: "ws",
        },
        {
          ts: 1_700_000_000_000,
          open: 101,
          high: 104,
          low: 100,
          close: 103,
          volume: 600,
          oi: null,
          source: "ws",
        },
      ],
    });
    const provider = new MarketHubDataProvider(RELIANCE);
    const out = await provider.getBars("NSE:RELIANCE", "15", { from: 1, to: 2, limit: 3 });

    expect(mockBars).toHaveBeenCalledWith({
      instrument_key: "NSE_EQ|INE002A01018",
      provider: "upstox",
      timeframe: "15",
      from: 1,
      to: 2,
      limit: 3,
    });
    expect(out).toEqual([
      { time: 1_699_999_000_000, open: 99, high: 102, low: 98, close: 101, volume: 500 },
      { time: 1_700_000_000_000, open: 101, high: 104, low: 100, close: 103, volume: 600 },
    ]);
    expect(out[0]).not.toHaveProperty("oi");
  });

  it("refuses to substitute an unknown symbol", async () => {
    mockResolve.mockResolvedValue(null);
    const onError = vi.fn();
    const provider = new MarketHubDataProvider(RELIANCE, onError);

    await expect(provider.getBars("NSE:NOPE", "5", { limit: 10 })).rejects.toThrow(
      "unknown instrument: NSE:NOPE",
    );
    expect(mockResolve).toHaveBeenCalledWith("NOPE", { exchange: "NSE" });
    expect(mockBars).not.toHaveBeenCalled();
    expect(onError).toHaveBeenLastCalledWith("unknown instrument: NSE:NOPE");
  });

  it("resolves a restored ticker through the canonical exact resolver", async () => {
    mockResolve.mockResolvedValue(INFY_ROW);
    mockBars.mockResolvedValue({
      provider: "fyers",
      instrument_key: "NSE:INFY-EQ",
      timeframe: 1,
      bars: [],
    });
    const provider = new MarketHubDataProvider(RELIANCE);

    const out = await provider.getBars("NSE:INFY", "1", { limit: 2 });

    expect(mockResolve).toHaveBeenCalledWith("INFY", { exchange: "NSE" });
    expect(mockBars).toHaveBeenCalledWith(
      expect.objectContaining({
        instrument_key: "NSE:INFY-EQ",
        provider: "fyers",
        timeframe: "1",
      }),
    );
    expect(out).toEqual([]);
  });

  it("surfaces a fetch failure to the host", async () => {
    mockBars.mockRejectedValue(new Error("bar history unavailable"));
    const onError = vi.fn();
    const provider = new MarketHubDataProvider(RELIANCE, onError);

    await expect(provider.getBars("NSE:RELIANCE", "15", { limit: 2 })).rejects.toThrow(
      "bar history unavailable",
    );
    expect(onError).toHaveBeenCalledWith("bar history unavailable");
  });

  it("reports symbol info and the supported timeframes", async () => {
    const provider = new MarketHubDataProvider(RELIANCE);

    await expect(provider.getSymbolInfo("NSE:RELIANCE")).resolves.toEqual({
      ticker: "NSE:RELIANCE",
      name: "RELIANCE",
      exchange: "NSE",
      type: "EQUITY",
    });
    expect(provider.info().supportedTimeframes).toEqual(CHART_TIMEFRAMES);
  });
});
