import { lazy, Suspense } from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { AiAlertsView } from "@/features/ai-alerts";
import { AlertsView } from "@/features/alerts";
import { BreadthView } from "@/features/breadth/BreadthView";
import { CatalogView } from "@/features/catalog/CatalogView";
import { ChatView } from "@/features/chat";
import { DashboardView } from "@/features/dashboard";
import { DiagnosticsView } from "@/features/diagnostics";
import { FnoWorkspaceView } from "@/features/fno/FnoWorkspaceView";
import { OptionChainView } from "@/features/fno/OptionChainView";
import { FuturesHeatmapView } from "@/features/futures-heatmap/FuturesHeatmapView";
import { InstrumentsView } from "@/features/instruments/InstrumentsView";
import { MarketDataView } from "@/features/market-data/MarketDataView";
import { MarketMapView } from "@/features/market-map/MarketMapView";
import { NewsView } from "@/features/news";
import { ScannersView } from "@/features/scanners/ScannersView";
import { SectorHeatmapView } from "@/features/sector-heatmap/SectorHeatmapView";
import { SentimentView } from "@/features/sentiment";
import { SettingsView } from "@/features/settings";
import { SubscriptionsView } from "@/features/subscriptions/SubscriptionsView";
import { WatchlistsView } from "@/features/watchlists";
import { AppShell } from "@/layouts/AppShell";

// The chart is the heaviest feature (Vela + renderer); keep it in its own chunk.
const ChartView = lazy(() =>
  import("@/features/chart/ChartView").then((m) => ({ default: m.ChartView })),
);

export function AppRouter() {
  return (
    <HashRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route
            path="/chart"
            element={
              <Suspense
                fallback={
                  <div className="state">
                    <div className="spinner" />
                    <span className="muted">Loading chart…</span>
                  </div>
                }
              >
                <ChartView />
              </Suspense>
            }
          />
          <Route path="/scanners" element={<ScannersView />} />
          <Route path="/market-map" element={<MarketMapView />} />
          <Route path="/breadth" element={<BreadthView />} />
          <Route path="/sector-heatmap" element={<SectorHeatmapView />} />
          <Route path="/futures-heatmap" element={<FuturesHeatmapView />} />
          <Route path="/fno" element={<FnoWorkspaceView />} />
          <Route path="/option-chain" element={<OptionChainView />} />
          <Route path="/subscriptions" element={<SubscriptionsView />} />
          <Route path="/market-data" element={<MarketDataView />} />
          <Route path="/instruments" element={<InstrumentsView />} />
          <Route path="/catalog" element={<CatalogView />} />
          <Route path="/news" element={<NewsView />} />
          <Route path="/dashboard" element={<DashboardView />} />
          <Route path="/watchlists" element={<WatchlistsView />} />
          <Route path="/alerts" element={<AlertsView />} />
          <Route path="/ai-alerts" element={<AiAlertsView />} />
          <Route path="/mcp" element={<Navigate to="/settings/ai-mcp" replace />} />
          <Route path="/logs" element={<Navigate to="/settings/logging" replace />} />
          <Route path="/diagnostics" element={<DiagnosticsView />} />
          <Route path="/chat" element={<ChatView />} />
          <Route path="/sentiment" element={<SentimentView />} />
          <Route path="/settings" element={<SettingsView />} />
          <Route path="/settings/:section" element={<SettingsView />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
