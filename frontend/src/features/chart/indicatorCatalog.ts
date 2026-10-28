// One chart indicator catalog.
//
// Vela registers the native types when a chart is constructed; this module
// turns those descriptors into the product catalog the picker searches and
// (Phase 4) the backend evaluators will key on. Read it lazily — the registry
// is empty until a chart exists, so building at import time would see nothing.

import {
  type InputSchema,
  type InputValue,
  nativeIndicatorDescriptors,
  type Translator,
} from "@luxalgo/vela";

export interface IndicatorCatalogEntry {
  /** Stable id: the Vela native type, and the Phase 4 evaluator key. */
  type: string;
  title: string;
  shortTitle?: string;
  category: string;
  /** Extra search terms beyond the title and short title. */
  aliases: string[];
  /** Display order inside the category (lower first; ties by title). */
  order: number;
  beta: boolean;
  multiInstance: boolean;
  /** No backend evaluator exists, so the indicator is chart-only. */
  chartOnly: boolean;
  backendReady: boolean;
  inputs: InputSchema[];
  defaults: Record<string, InputValue>;
}

export const INDICATOR_CATEGORY_ORDER = [
  "Moving averages",
  "Bands & channels",
  "Oscillators",
  "Trend",
  "Volatility",
  "Volume",
  "Overlays",
  "Other",
];

const CATEGORY_TYPES: Record<string, string[]> = {
  "Moving averages": [
    "sma",
    "ema",
    "moving-average",
    "rma",
    "zlema",
    "vidya",
    "ma-envelope",
    "lsma",
    "linear-regression-slope",
  ],
  "Bands & channels": [
    "bollinger-bands",
    "percent-b",
    "bandwidth",
    "keltner-channels",
    "donchian-channels",
    "chandelier-exit",
    "chande-kroll-stop",
    "supertrend",
    "williams-alligator",
  ],
  Oscillators: [
    "rsi",
    "stochastic",
    "stochastic-rsi",
    "macd",
    "ppo",
    "awesome-oscillator",
    "commodity-channel-index",
    "williams-percent-r",
    "chande-momentum-oscillator",
    "connors-rsi",
    "fisher-transform",
    "trix",
    "true-strength-index",
    "smi-ergodic",
    "schaff-trend-cycle",
    "know-sure-thing",
    "coppock-curve",
    "detrended-price-oscillator",
    "rate-of-change",
    "ultimate-oscillator",
    "relative-vigor-index",
    "relative-volatility-index",
    "balance-of-power",
    "elder-ray",
    "ttm-squeeze",
    "gator-oscillator",
  ],
  Trend: ["aroon", "average-directional-index", "vortex-indicator"],
  Volatility: [
    "average-true-range",
    "historical-volatility",
    "chaikin-volatility",
    "mass-index",
    "standard-deviation",
    "ulcer-index",
    "choppiness-index",
  ],
  Volume: [
    "volume",
    "vpvr",
    "on-balance-volume",
    "accumulation-distribution",
    "chaikin-money-flow",
    "chaikin-oscillator",
    "ease-of-movement",
    "force-index",
    "klinger-oscillator",
    "money-flow-index",
    "negative-volume-index",
    "positive-volume-index",
    "price-volume-trend",
    "volume-oscillator",
    "pvo",
    "volume-flow-indicator",
    "intraday-intensity",
  ],
  Overlays: [
    "parabolic-sar",
    "vwap",
    "pivot-points",
    "52-week-high-low",
    "zigzag",
    "williams-fractal",
  ],
};

const TYPE_CATEGORY = new Map<string, string>();
for (const [category, types] of Object.entries(CATEGORY_TYPES)) {
  for (const type of types) TYPE_CATEGORY.set(type, category);
}

/** Product ordering inside a category; the rest sort alphabetically after. */
const POPULAR_ORDER: Record<string, number> = {
  volume: 10,
  sma: 20,
  ema: 30,
  rsi: 40,
  macd: 50,
  "bollinger-bands": 60,
  "average-true-range": 70,
  vwap: 80,
  supertrend: 90,
  stochastic: 100,
  "average-directional-index": 110,
};

const ALIASES: Record<string, string[]> = {
  rsi: ["relative strength"],
  macd: ["moving average convergence divergence"],
  "bollinger-bands": ["bb", "bollinger"],
  "average-true-range": ["atr"],
  "average-directional-index": ["adx"],
  "on-balance-volume": ["obv"],
  "money-flow-index": ["mfi"],
  vwap: ["volume weighted average price"],
  "stochastic-rsi": ["stoch rsi"],
  vpvr: ["volume profile", "vrvp"],
  "52-week-high-low": ["year high", "year low"],
};

/** Types with a backend evaluator (Phase 4). Everything else is chart-only. */
const BACKEND_READY = new Set<string>();

function categoryIndex(category: string): number {
  const index = INDICATOR_CATEGORY_ORDER.indexOf(category);
  return index < 0 ? INDICATOR_CATEGORY_ORDER.length : index;
}

export function buildIndicatorCatalog(i18n?: Translator): IndicatorCatalogEntry[] {
  return nativeIndicatorDescriptors(i18n)
    .map((descriptor) => {
      const category = TYPE_CATEGORY.get(descriptor.type) ?? "Other";
      const shortTitle = descriptor.shortTitle;
      const backendReady = BACKEND_READY.has(descriptor.type);
      return {
        type: descriptor.type,
        title: descriptor.title,
        ...(shortTitle ? { shortTitle } : {}),
        category,
        aliases: [...(ALIASES[descriptor.type] ?? []), ...(shortTitle ? [shortTitle] : [])],
        order: POPULAR_ORDER[descriptor.type] ?? 500,
        beta: descriptor.beta === true,
        multiInstance: descriptor.multiInstance === true,
        chartOnly: !backendReady,
        backendReady,
        inputs: descriptor.inputsSchema(),
        defaults: descriptor.defaultInputs(),
      };
    })
    .sort(
      (a, b) =>
        categoryIndex(a.category) - categoryIndex(b.category) ||
        a.order - b.order ||
        a.title.localeCompare(b.title),
    );
}
