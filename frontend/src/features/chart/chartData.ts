// Vela data provider over MarketHub's persisted bar store.
//
// The chart speaks bare minute timeframes ("1", "5", … "43200") and a
// provider-prefixed ticker. MarketHub identity is the canonical instrument
// token, so the ticker carries the human `EXCHANGE:TRADINGSYMBOL` pair and the
// provider maps it back to the exact token via the instrument catalogue — no
// fabricated tokens and no guessing an instrument class.
//
// Every bar Vela sees is provider truth from GET /api/market/bars: stored bars
// at the provider's own granularity, with the live forming bar folded in and
// week/month rolled up from daily by the backend.

import type { BarRange, DataProvider, OHLCV, ProviderInfo, SymbolInfo } from "@luxalgo/vela";
import { getMarketBars, resolveMarketInstrument, toInstrument } from "@/api/market";
import type { Instrument, MarketBar } from "@/types";

export const VELA_PROVIDER = "markethub";

// Upstox's native V3 timeframes (minutes): 1m-4h intraday, daily, and the
// backend-derived week/month roll-ups.
export const CHART_TIMEFRAMES: string[] = [
  "1",
  "5",
  "15",
  "30",
  "60",
  "240",
  "1440",
  "10080",
  "43200",
];

/** Human, unique-per-venue ticker: `NSE:RELIANCE`, `BSE:SENSEX`. */
export function tickerFor(instrument: Instrument): string {
  return `${instrument.exchange}:${instrument.tradingsymbol}`;
}

/** The provider-prefixed symbol Vela resolves (`markethub:NSE:RELIANCE`). */
export function chartSymbol(instrument: Instrument): string {
  return `${VELA_PROVIDER}:${tickerFor(instrument)}`;
}

function splitTicker(ticker: string): { exchange: string; symbol: string } {
  const at = ticker.indexOf(":");
  if (at <= 0) return { exchange: "", symbol: ticker };
  return { exchange: ticker.slice(0, at), symbol: ticker.slice(at + 1) };
}

function toOhlcv(bars: MarketBar[]): OHLCV[] {
  const out: OHLCV[] = [];
  for (const bar of [...bars].sort((a, b) => a.ts - b.ts)) {
    if (!Number.isFinite(bar.ts) || bar.close <= 0) continue;
    const mapped: OHLCV = {
      time: bar.ts,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    };
    if (bar.volume !== null) mapped.volume = bar.volume;
    // The API is already ordered + deduplicated; this keeps the port contract
    // even if a provider ever repeats the forming bar in one response.
    const last = out[out.length - 1];
    if (last && last.time === mapped.time) out[out.length - 1] = mapped;
    else out.push(mapped);
  }
  return out;
}

function errorMessage(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return "Chart data is unavailable.";
}

export class MarketHubDataProvider implements DataProvider {
  private readonly known = new Map<string, Instrument>();
  private readonly onError?: (message: string | null) => void;

  constructor(seed: Instrument, onError?: (message: string | null) => void) {
    this.onError = onError;
    this.known.set(tickerFor(seed), seed);
  }

  /** Remember an exact catalogue identity (from the selector or URL). */
  remember(instrument: Instrument): Instrument {
    this.known.set(tickerFor(instrument), instrument);
    return instrument;
  }

  /**
   * Resolve a ticker to its canonical instrument. Known instruments (seeded
   * from the picker / deep link) are exact; a restored chart state is resolved
   * through the canonical exact resolver — an unknown symbol is an error,
   * never a substitution.
   */
  private async resolve(ticker: string): Promise<Instrument> {
    const cached = this.known.get(ticker);
    if (cached) return cached;
    const { exchange, symbol } = splitTicker(ticker);
    if (!symbol) throw new Error(`unknown instrument: ${ticker}`);
    const found = await resolveMarketInstrument(symbol, {
      exchange: exchange || undefined,
    });
    if (!found) throw new Error(`unknown instrument: ${ticker}`);
    return this.remember(toInstrument(found));
  }

  async getBars(ticker: string, timeframe: string, range: BarRange): Promise<OHLCV[]> {
    try {
      const instrument = await this.resolve(ticker);
      const response = await getMarketBars({
        instrument_key: instrument.instrument_token,
        provider: instrument.provider,
        timeframe,
        from: range.from,
        to: range.to,
        limit: range.limit,
      });
      this.onError?.(null);
      return toOhlcv(response.bars);
    } catch (err) {
      this.onError?.(errorMessage(err));
      throw err;
    }
  }

  async getSymbolInfo(ticker: string): Promise<SymbolInfo | undefined> {
    try {
      const instrument = await this.resolve(ticker);
      return {
        ticker,
        name: instrument.tradingsymbol,
        exchange: instrument.exchange,
        type: instrument.instrument_type,
      };
    } catch {
      return undefined;
    }
  }

  info(): ProviderInfo {
    return {
      name: VELA_PROVIDER,
      displayName: "MarketHub",
      supportedTimeframes: CHART_TIMEFRAMES,
      capabilities: { enumerate: false, stream: false, symbolInfo: true },
    };
  }
}
