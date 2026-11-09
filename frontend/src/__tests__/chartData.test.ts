import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/market", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/market")>();
  return {
    ...actual,
    getMarketBars: vi.fn(),
    searchMarketInstruments: vi.fn(),
  };
});

import { getMarketBars, searchMarketInstruments } from "@/api/market";
import {
  CHART_TIMEFRAMES,
  chartSymbol,
  MarketHubDataProvider,
  poolSymbolToDescriptor,
  poolSymbolToInstrument,
  tickerFor,
} from "@/features/chart/chartData";
import type { Instrument, MarketInstrument, PoolSymbol } from "@/types";

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
const mockSearch = searchMarketInstruments as ReturnType<typeof vi.fn>;

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
    mockSearch.mockResolvedValue([]);
    const onError = vi.fn();
    const provider = new MarketHubDataProvider(RELIANCE, onError);

    await expect(provider.getBars("NSE:NOPE", "5", { limit: 10 })).rejects.toThrow(
      "unknown instrument: NSE:NOPE",
    );
    expect(mockSearch).toHaveBeenCalledWith({ q: "NOPE", exchange: "NSE", exact: true, limit: 20 });
    expect(mockBars).not.toHaveBeenCalled();
    expect(onError).toHaveBeenLastCalledWith("unknown instrument: NSE:NOPE");
  });

  it("resolves a restored ticker through the canonical exact resolver", async () => {
    mockSearch.mockResolvedValue([INFY_ROW]);
    mockBars.mockResolvedValue({
      provider: "fyers",
      instrument_key: "NSE:INFY-EQ",
      timeframe: 1,
      bars: [],
    });
    const provider = new MarketHubDataProvider(RELIANCE);

    const out = await provider.getBars("NSE:INFY", "1", { limit: 2 });

    expect(mockSearch).toHaveBeenCalledWith({ q: "INFY", exchange: "NSE", exact: true, limit: 20 });
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
    expect(provider.info().capabilities.enumerate).toBe(true);
  });
});

describe("Pool integration (Phase 3)", () => {
  const REL_POOL: PoolSymbol = {
    ticker: "RELIANCE",
    description: "RELIANCE INDUSTRIES LTD",
    type: "stock",
    group: "EQUITY",
    prefix: "NSE",
    provider: "upstox",
    provider_token: "NSE_EQ|INE002A01018",
    instrument_key: "NSE:NSE_EQ|INE002A01018",
    exchange: "NSE",
    instrument_type: "EQUITY",
    symbol: "RELIANCE",
  };
  const NIFTY_POOL: PoolSymbol = {
    ticker: "NIFTY",
    description: "Nifty 50",
    type: "index",
    group: "INDEX",
    prefix: "NSE",
    provider: "upstox",
    provider_token: "NSE_INDEX|Nifty 50",
    instrument_key: "NSE:NSE_INDEX|Nifty 50",
    exchange: "NSE",
    instrument_type: "INDEX",
    symbol: "NIFTY",
  };
  const ETF_POOL: PoolSymbol = {
    ticker: "NIFTYBEES",
    description: "NIPPON INDIA ETF NIFTY 50 BEES",
    type: "etf",
    group: "ETF",
    prefix: "NSE",
    provider: "fyers",
    provider_token: "101000000010576",
    instrument_key: "NSE:101000000010576",
    exchange: "NSE",
    instrument_type: "ETF",
    symbol: "NIFTYBEES",
  };

  it("maps pool rows to Vela descriptors and Instruments", () => {
    expect(poolSymbolToDescriptor(REL_POOL)).toEqual({
      ticker: "RELIANCE",
      description: "RELIANCE INDUSTRIES LTD",
      type: "stock",
      group: "EQUITY",
      prefix: "NSE",
    });
    expect(poolSymbolToInstrument(REL_POOL)).toEqual({
      instrument_token: "NSE_EQ|INE002A01018",
      exchange: "NSE",
      tradingsymbol: "RELIANCE",
      instrument_type: "EQUITY",
      provider: "upstox",
    });
  });

  it("seeds pool, exposes listSymbols, and resolves native-picker tickers to exact identity", async () => {
    mockBars.mockResolvedValue({
      provider: "upstox",
      instrument_key: "NSE_EQ|INE002A01018",
      timeframe: 15,
      bars: [],
    });
    const provider = new MarketHubDataProvider(RELIANCE);
    const changed = provider.setPool([REL_POOL, NIFTY_POOL]);
    expect(changed).toBe(true);
    const symbols = await provider.listSymbols();
    expect(symbols.map((s) => s.ticker)).toEqual(["RELIANCE", "NIFTY"]);
    // Vela native picker passes the bare pool ticker.
    await provider.getBars("RELIANCE", "15", { limit: 1 });
    expect(mockBars).toHaveBeenCalledWith(
      expect.objectContaining({
        instrument_key: "NSE_EQ|INE002A01018",
        provider: "upstox",
      }),
    );
  });

  it("handles duplicate descriptors and identical pools without unnecessary re-registration", () => {
    const provider = new MarketHubDataProvider(RELIANCE);
    expect(provider.setPool([REL_POOL, REL_POOL, NIFTY_POOL])).toBe(true);
    // identical content => no change
    expect(provider.setPool([REL_POOL, REL_POOL, NIFTY_POOL])).toBe(false);
  });

  it("handles empty pool", async () => {
    const provider = new MarketHubDataProvider(RELIANCE);
    // Initial state is already empty; setting empty again is not a change.
    expect(provider.setPool([])).toBe(false);
    await expect(provider.listSymbols()).resolves.toEqual([]);
    // A non-empty pool then cleared is a change.
    provider.setPool([REL_POOL]);
    expect(provider.setPool([])).toBe(true);
    await expect(provider.listSymbols()).resolves.toEqual([]);
  });

  it("shows descriptors even when pool lacks identity fields (resilience)", async () => {
    // Older backend / partial response: Vela picker must still list symbols.
    const provider = new MarketHubDataProvider(RELIANCE);
    provider.setPool([
      {
        ticker: "RELIANCE",
        description: "RELIANCE INDUSTRIES LTD",
        type: "stock",
        group: "EQUITY",
        prefix: "NSE",
      },
      { ticker: "NIFTY", description: "Nifty 50", type: "index", group: "INDEX", prefix: "NSE" },
    ]);
    const symbols = await provider.listSymbols();
    expect(symbols.map((s) => s.ticker)).toEqual(["RELIANCE", "NIFTY"]);
    expect(symbols[0]).toMatchObject({
      description: "RELIANCE INDUSTRIES LTD",
      type: "stock",
      prefix: "NSE",
    });
  });

  it("resolves Upstox index, Upstox ETF-like equity, and Fyers ETF with correct provider tokens", async () => {
    mockBars.mockResolvedValue({ provider: "x", instrument_key: "x", timeframe: 1, bars: [] });
    const provider = new MarketHubDataProvider(RELIANCE);
    provider.setPool([NIFTY_POOL, REL_POOL, ETF_POOL]);

    await provider.getBars("NIFTY", "1", { limit: 1 });
    expect(mockBars).toHaveBeenLastCalledWith(
      expect.objectContaining({ instrument_key: "NSE_INDEX|Nifty 50", provider: "upstox" }),
    );
    await provider.getBars("RELIANCE", "1", { limit: 1 });
    expect(mockBars).toHaveBeenLastCalledWith(
      expect.objectContaining({ instrument_key: "NSE_EQ|INE002A01018", provider: "upstox" }),
    );
    await provider.getBars("NIFTYBEES", "1", { limit: 1 });
    expect(mockBars).toHaveBeenLastCalledWith(
      expect.objectContaining({ instrument_key: "101000000010576", provider: "fyers" }),
    );
  });

  it("never silently substitutes when exact resolution is ambiguous (collision safety)", async () => {
    // Bare ticker not in pool/known resolves via exact search; multiple identities => error.
    mockSearch.mockResolvedValue([
      {
        ...INFY_ROW,
        symbol: "ENERGY",
        provider: "upstox",
        provider_token: "BSE_INDEX|ENERGY",
        exchange: "BSE",
        type: "INDEX",
      },
      {
        ...INFY_ROW,
        symbol: "ENERGY",
        provider: "upstox",
        provider_token: "NSE_EQ|X",
        exchange: "NSE",
        type: "EQUITY",
      },
      {
        ...INFY_ROW,
        symbol: "ENERGY",
        provider: "fyers",
        provider_token: "101",
        exchange: "NSE",
        type: "ETF",
      },
    ]);
    const onError = vi.fn();
    const provider = new MarketHubDataProvider(RELIANCE, onError);
    await expect(provider.getBars("ENERGY", "1", { limit: 1 })).rejects.toThrow(/Ambiguous symbol/);
    expect(mockBars).not.toHaveBeenCalled();
    expect(onError).toHaveBeenCalled();
  });

  it("resolves pool collision tickers (ENERGY/INFRA/METAL/MID150) to the pool's single unambiguous identity", async () => {
    // Pool exposes only one identity per ticker (BSE INDEX) — native picker must resolve to it.
    const collisionPools: PoolSymbol[] = ["ENERGY", "INFRA", "METAL", "MID150"].map((sym) => ({
      ticker: sym,
      description: `BSE ${sym}`,
      type: "index",
      group: "INDEX",
      prefix: "BSE",
      provider: "upstox",
      provider_token: `BSE_INDEX|${sym}`,
      instrument_key: `BSE:BSE_INDEX|${sym}`,
      exchange: "BSE",
      instrument_type: "INDEX",
      symbol: sym,
    }));
    mockBars.mockImplementation(async (p: { instrument_key: string; provider?: string }) => ({
      provider: p.provider ?? "upstox",
      instrument_key: p.instrument_key,
      timeframe: 1,
      bars: [],
    }));
    const provider = new MarketHubDataProvider(RELIANCE);
    provider.setPool(collisionPools);
    for (const sym of ["ENERGY", "INFRA", "METAL", "MID150"]) {
      await provider.getBars(sym, "1", { limit: 1 });
      expect(mockBars).toHaveBeenLastCalledWith(
        expect.objectContaining({ instrument_key: `BSE_INDEX|${sym}`, provider: "upstox" }),
      );
    }
    expect(mockSearch).not.toHaveBeenCalled(); // resolved from pool, no ambiguous search
  });

  it("fails safely (no first-match substitution) for ambiguous ENERGY/INFRA/METAL/MID150 outside pool", async () => {
    const onError = vi.fn();
    const provider = new MarketHubDataProvider(RELIANCE, onError);
    for (const sym of ["ENERGY", "INFRA", "METAL", "MID150"]) {
      mockSearch.mockResolvedValueOnce([
        {
          instrument_key: `BSE:BSE_INDEX|${sym}`,
          provider: "upstox",
          provider_token: `BSE_INDEX|${sym}`,
          symbol: sym,
          exchange: "BSE",
          type: "INDEX",
        },
        {
          instrument_key: `NSE:NSE_EQ|X${sym}`,
          provider: "upstox",
          provider_token: `NSE_EQ|X${sym}`,
          symbol: sym,
          exchange: "NSE",
          type: "EQUITY",
        },
        {
          instrument_key: `NSE:101${sym}`,
          provider: "fyers",
          provider_token: `101${sym}`,
          symbol: sym,
          exchange: "NSE",
          type: "ETF",
        },
      ]);
      await expect(provider.getBars(sym, "1", { limit: 1 })).rejects.toThrow(/Ambiguous symbol/);
    }
    expect(mockBars).not.toHaveBeenCalled();
  });
});
