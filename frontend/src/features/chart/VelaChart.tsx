// The Vela workspace mount: one chart, host-driven symbol switching, Vela's
// native topbar/legend/drawings/indicators. The React layer owns identity and
// error reporting; every market fact comes through MarketHubDataProvider.
//
// The two chart contributions are imported for their import-time registration:
// the symbol action/attachment and the catalog-driven indicator slot override.
//
// Phase 3: the curated pool (GET /api/instruments/pool) feeds Vela's native
// symbol picker via listSymbols(). Pool load is async/non-blocking and cached
// (never re-fetched on instrument changes); failure/empty never breaks the chart
// (MarketHub catalog remains the fallback). Native-picker selections sync back
// to React state through market:changed, resolving to the exact pre-seeded
// identity (never a same-ticker substitution).

import { VelaWorkspace } from "@luxalgo/vela/workspace";
import { useEffect, useRef } from "react";
import type { ThemeMode } from "@/app/ThemeProvider";
import { getInstrumentPool } from "@/api/market";
import type { Instrument, PoolSymbol } from "@/types";
import { bindChartSymbolHandlers, MARKETHUB_SYMBOL_ACTION_ID } from "./chartSymbolPicker";
import "./indicatorPicker";
import { CHART_TIMEFRAMES, chartSymbol, MarketHubDataProvider, VELA_PROVIDER } from "./chartData";

let poolCache: Promise<PoolSymbol[]> | null = null;

function loadPoolOnce(): Promise<PoolSymbol[]> {
  if (!poolCache) {
    poolCache = getInstrumentPool({ limit: 1000 })
      .then((r) => r.symbols ?? [])
      .catch(() => {
        poolCache = null; // allow retry on next mount
        return [] as PoolSymbol[];
      });
  }
  return poolCache;
}

function stripProviderPrefix(symbol: string): string {
  const prefix = `${VELA_PROVIDER}:`;
  return symbol.startsWith(prefix) ? symbol.slice(prefix.length) : symbol;
}

function sameInstrument(a: Instrument, b: Instrument): boolean {
  return (
    a.instrument_token === b.instrument_token &&
    a.exchange === b.exchange &&
    (a.provider ?? "") === (b.provider ?? "")
  );
}

export function VelaChart({
  instrument,
  theme,
  onSelect,
  onError,
}: {
  instrument: Instrument;
  theme: ThemeMode;
  onSelect: (instrument: Instrument) => void;
  onError: (message: string | null) => void;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const workspaceRef = useRef<VelaWorkspace | null>(null);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const instrumentRef = useRef(instrument);
  instrumentRef.current = instrument;
  const themeRef = useRef(theme);
  themeRef.current = theme;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const provider = new MarketHubDataProvider(instrument, (message) =>
      onErrorRef.current(message),
    );
    let workspace: VelaWorkspace;
    try {
      workspace = new VelaWorkspace(host, {
        layout: false,
        symbol: chartSymbol(instrument),
        timeframe: "15",
        live: true,
        theme: themeRef.current,
        volume: true,
        // Indian markets only: bar labels and the session cursor are IST.
        timezone: "Asia/Kolkata",
        // A dedicated chart page: keyboard pan/zoom works without a click.
        autofocus: true,
        timeframes: CHART_TIMEFRAMES,
        providers: { [VELA_PROVIDER]: () => provider },
        // Single catalog across the app: the MarketHub symbol action opens the
        // shared InstrumentPicker (full catalog + structured F&O). Vela's native
        // symbol slot is not shown to avoid duplicate catalog UIs.
        topbar: {
          left: ["timeframes", MARKETHUB_SYMBOL_ACTION_ID, "style", "indicators", "undo-redo"],
        },
        // Layout, style, drawings and indicators are remembered per instrument.
        persist: `markethub-chart:${instrument.instrument_token}`,
      });
    } catch (err) {
      onErrorRef.current(err instanceof Error ? err.message : "Chart failed to start.");
      return;
    }
    workspaceRef.current = workspace;
    const unbind = bindChartSymbolHandlers(host, {
      onSelect: (next) => onSelectRef.current(next),
    });

    // Sync Vela's native picker selections back to React state. The provider's
    // known map (seeded from the pool) resolves the ticker to the exact identity.
    const chart = workspace.active.chart;
    const offMarket = chart.on("market:changed", ({ symbol }) => {
      const raw = stripProviderPrefix(symbol);
      const resolved = provider.resolveKnown(raw) ?? provider.resolveKnown(raw.toUpperCase());
      if (!resolved) return;
      if (sameInstrument(resolved, instrumentRef.current)) return;
      onSelectRef.current(resolved);
    });

    // Seed cached pool immediately if available; otherwise fetch async.
    let cancelled = false;
    void (async () => {
      const symbols = await loadPoolOnce();
      if (cancelled || !workspaceRef.current) return;
      const changed = provider.setPool(symbols);
      if (changed) {
        try {
          // Re-register to rebuild Vela's eager symbol index from listSymbols().
          workspaceRef.current.active.chart.data.registerProvider(VELA_PROVIDER, provider);
        } catch {
          // Index rebuild failure must not break the chart.
        }
      }
    })();

    return () => {
      cancelled = true;
      offMarket();
      unbind();
      workspaceRef.current = null;
      workspace.destroy();
    };
  }, [instrument]);

  useEffect(() => {
    workspaceRef.current?.setTheme(theme);
  }, [theme]);

  return <div ref={hostRef} className="chart-vela" />;
}
