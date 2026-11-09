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
//
// Phase 3: Vela's native picker is fed a curated pool (GET /api/instruments/pool)
// via `listSymbols()`. Each descriptor's ticker is the pool ticker; the provider
// pre-seeds an exact Instrument mapping so `getBars` resolves to the correct
// provider + bare provider_token. Same-ticker collisions are handled by exposing
// only the pool's single unambiguous identity per ticker (never first-match).

import type {
  BarRange,
  DataProvider,
  OHLCV,
  ProviderInfo,
  SymbolDescriptor,
  SymbolInfo,
} from "@luxalgo/vela";
import { getMarketBars, searchMarketInstruments, toInstrument } from "@/api/market";
import type { Instrument, MarketBar, PoolSymbol } from "@/types";

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

/** Map a pool row (with exact identity) to the chart's Instrument shape. */
export function poolSymbolToInstrument(row: PoolSymbol): Instrument | null {
  const token = row.provider_token;
  const exchange = row.exchange ?? row.prefix;
  const symbol = row.symbol ?? row.ticker;
  const type = row.instrument_type ?? row.group;
  if (!token || !exchange || !symbol || !type) return null;
  return {
    instrument_token: token,
    exchange,
    tradingsymbol: symbol,
    instrument_type: type as Instrument["instrument_type"],
    provider: row.provider,
  };
}

/** Map a pool row to a Vela SymbolDescriptor. Ticker is the pool ticker (what
 * Vela passes to getBars); the provider resolves it via the pre-seeded identity. */
export function poolSymbolToDescriptor(row: PoolSymbol): SymbolDescriptor | null {
  if (!row.ticker) return null;
  return {
    ticker: row.ticker,
    description: row.description,
    type: row.type,
    group: row.group,
    prefix: row.prefix,
  };
}

export class MarketHubDataProvider implements DataProvider {
  private readonly known = new Map<string, Instrument>();
  private readonly poolByTicker = new Map<string, Instrument>();
  private readonly poolMetaByTicker = new Map<string, PoolSymbol>();
  private poolDescriptors: SymbolDescriptor[] = [];
  private poolKey = "";
  private readonly onError?: (message: string | null) => void;

  constructor(seed: Instrument, onError?: (message: string | null) => void) {
    this.onError = onError;
    this.remember(seed);
  }

  /** Remember an exact catalogue identity (from the selector, URL, or pool). */
  remember(instrument: Instrument): Instrument {
    this.known.set(tickerFor(instrument), instrument);
    this.known.set(instrument.tradingsymbol.toUpperCase(), instrument);
    return instrument;
  }

  /** Seed the curated pool. Returns true if the descriptor set changed. */
  setPool(symbols: PoolSymbol[]): boolean {
    const key = symbols
      .map((s) => `${s.ticker}|${s.provider ?? ""}|${s.provider_token ?? ""}`)
      .join("\n");
    if (key === this.poolKey) return false;
    this.poolKey = key;
    this.poolDescriptors = [];
    this.poolByTicker.clear();
    this.poolMetaByTicker.clear();
    for (const row of symbols) {
      const descriptor = poolSymbolToDescriptor(row);
      if (!descriptor) continue;
      // Always expose the descriptor so Vela's native picker shows symbols,
      // even when identity fields are unavailable (resilience / old backends).
      this.poolDescriptors.push(descriptor);
      this.poolMetaByTicker.set(row.ticker, row);
      this.poolMetaByTicker.set(row.ticker.toUpperCase(), row);
      const instrument = poolSymbolToInstrument(row);
      if (!instrument) continue;
      // Pool ticker is authoritative for Vela native-picker resolution.
      this.poolByTicker.set(row.ticker, instrument);
      this.poolByTicker.set(row.ticker.toUpperCase(), instrument);
      this.known.set(tickerFor(instrument), instrument);
    }
    return true;
  }

  /** Resolve a known ticker to its Instrument (pool/remembered), or null. */
  resolveKnown(ticker: string): Instrument | null {
    return (
      this.poolByTicker.get(ticker) ??
      this.poolByTicker.get(ticker.toUpperCase()) ??
      this.known.get(ticker) ??
      this.known.get(ticker.toUpperCase()) ??
      null
    );
  }

  /**
   * Resolve a ticker to its canonical instrument. Known instruments (seeded
   * from the picker / deep link / pool) are exact; a restored chart state is
   * resolved through the canonical exact resolver — an unknown or ambiguous
   * symbol is an error, never a substitution.
   */
  private async resolve(ticker: string): Promise<Instrument> {
    const cached = this.resolveKnown(ticker);
    if (cached) return cached;
    const { exchange, symbol } = splitTicker(ticker);
    if (!symbol) throw new Error(`unknown instrument: ${ticker}`);
    // Use pool descriptor metadata as a hint when identity wasn't pre-seeded
    // (e.g. older backend response). Never guess: ambiguity still fails safe.
    const meta =
      this.poolMetaByTicker.get(ticker) ?? this.poolMetaByTicker.get(ticker.toUpperCase());
    const hintExchange = exchange || meta?.exchange || meta?.prefix;
    const hintType = meta?.instrument_type ?? (meta?.group ? meta.group.toUpperCase() : undefined);
    // Exact resolution only. Never substitute first match: if multiple
    // identities share this symbol, require the full MarketHub catalog.
    const rows = await searchMarketInstruments({
      q: symbol,
      exchange: hintExchange || undefined,
      exact: true,
      limit: 20,
    });
    let candidates = rows;
    if (hintType && candidates.length > 1) {
      const typed = candidates.filter((r) => r.type.toUpperCase() === hintType.toUpperCase());
      if (typed.length === 1) candidates = typed;
    }
    if (candidates.length === 0) throw new Error(`unknown instrument: ${ticker}`);
    if (candidates.length > 1) {
      // Ambiguous: multiple distinct identities. Do not guess.
      throw new Error(
        `Ambiguous symbol "${ticker}" has multiple instruments. Use the MarketHub catalog to choose the exact exchange/type/provider.`,
      );
    }
    const match = candidates[0];
    if (!match) throw new Error(`unknown instrument: ${ticker}`);
    return this.remember(toInstrument(match));
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

  async listSymbols(): Promise<SymbolDescriptor[]> {
    return this.poolDescriptors;
  }

  info(): ProviderInfo {
    return {
      name: VELA_PROVIDER,
      displayName: "MarketHub",
      supportedTimeframes: CHART_TIMEFRAMES,
      capabilities: { enumerate: true, stream: false, symbolInfo: true },
    };
  }
}
