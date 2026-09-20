import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { AiAlertsView } from "@/features/ai-alerts";
import { AlertsView } from "@/features/alerts";
import { BreadthView } from "@/features/breadth/BreadthView";
import { ChartsView } from "@/features/charts/ChartsView";
import { ChatView } from "@/features/chat";
import { DashboardView } from "@/features/dashboard";
import { DiagnosticsView } from "@/features/diagnostics";
import { FnoWorkspaceView } from "@/features/fno/FnoWorkspaceView";
import { OptionChainView } from "@/features/fno/OptionChainView";
import { InstrumentsView } from "@/features/instruments/InstrumentsView";
import { MarketMapView } from "@/features/market-map/MarketMapView";
import { NewsView } from "@/features/news";
import { ScannersView } from "@/features/scanners/ScannersView";
import { SectorHeatmapView } from "@/features/sector-heatmap/SectorHeatmapView";
import { SentimentView } from "@/features/sentiment";
import { SettingsView } from "@/features/settings";
import { SubscriptionsView } from "@/features/subscriptions/SubscriptionsView";
import { WatchlistsView } from "@/features/watchlists";
import { AppShell } from "@/layouts/AppShell";

export function AppRouter() {
  return (
    <HashRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="/charts" element={<ChartsView />} />
          <Route path="/scanners" element={<ScannersView />} />
          <Route path="/market-map" element={<MarketMapView />} />
          <Route path="/breadth" element={<BreadthView />} />
          <Route path="/sector-heatmap" element={<SectorHeatmapView />} />
          <Route path="/fno" element={<FnoWorkspaceView />} />
          <Route path="/option-chain" element={<OptionChainView />} />
          <Route path="/subscriptions" element={<SubscriptionsView />} />
          <Route path="/instruments" element={<InstrumentsView />} />
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
