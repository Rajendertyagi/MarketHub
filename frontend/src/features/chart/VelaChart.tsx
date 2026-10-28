// The Vela workspace mount: one chart, host-driven symbol switching, Vela's
// native topbar/legend/drawings/indicators. The React layer owns identity and
// error reporting; every market fact comes through MarketHubDataProvider.
//
// The two chart contributions are imported for their import-time registration:
// the symbol action/attachment and the catalog-driven indicator slot override.

import { VelaWorkspace } from "@luxalgo/vela/workspace";
import { useEffect, useRef } from "react";
import type { ThemeMode } from "@/app/ThemeProvider";
import type { Instrument } from "@/types";
import { bindChartSymbolHandlers, MARKETHUB_SYMBOL_ACTION_ID } from "./chartSymbolPicker";
import "./indicatorPicker";
import { CHART_TIMEFRAMES, chartSymbol, MarketHubDataProvider, VELA_PROVIDER } from "./chartData";

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
        // Symbol selection is a MarketHub contribution (the provider index is
        // server-side), so Vela's stateful symbol slot stays out of the topbar.
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
    return () => {
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
