import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/layouts/AppShell";
import { ChartsView } from "@/features/charts/ChartsView";
import { ScannersView } from "@/features/scanners/ScannersView";
import { BreadthView } from "@/features/breadth/BreadthView";
import { SectorHeatmapView } from "@/features/sector-heatmap/SectorHeatmapView";
import { MarketMapView } from "@/features/market-map/MarketMapView";
import { FnoWorkspaceView } from "@/features/fno/FnoWorkspaceView";
import { OptionChainView } from "@/features/fno/OptionChainView";
import { SubscriptionsView } from "@/features/subscriptions/SubscriptionsView";
import { InstrumentsView } from "@/features/instruments/InstrumentsView";
import { NewsView } from "@/features/news";
import { DashboardView } from "@/features/dashboard";
import { WatchlistsView } from "@/features/watchlists";
import { AlertsView } from "@/features/alerts";
import { AiAlertsView } from "@/features/ai-alerts";
import { DiagnosticsView } from "@/features/diagnostics";
import { ChatView } from "@/features/chat";
import { SentimentView } from "@/features/sentiment";
import { SettingsView } from "@/features/settings";

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
