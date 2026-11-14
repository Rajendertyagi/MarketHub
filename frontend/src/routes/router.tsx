import { lazy, Suspense, type ReactNode } from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { DashboardView } from "@/features/dashboard";
import { AppShell } from "@/layouts/AppShell";

function RouteFallback({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state">
      <div className="spinner" />
      <span className="muted">{label}</span>
    </div>
  );
}

const ChartView = lazy(() => import("@/features/chart/ChartView").then((m) => ({ default: m.ChartView })));
const ScannersView = lazy(() => import("@/features/scanners/ScannersView").then((m) => ({ default: m.ScannersView })));
const MarketMapView = lazy(() => import("@/features/market-map/MarketMapView").then((m) => ({ default: m.MarketMapView })));
const BreadthView = lazy(() => import("@/features/breadth/BreadthView").then((m) => ({ default: m.BreadthView })));
const SectorHeatmapView = lazy(() =>
  import("@/features/sector-heatmap/SectorHeatmapView").then((m) => ({ default: m.SectorHeatmapView })),
);
const FuturesHeatmapView = lazy(() =>
  import("@/features/futures-heatmap/FuturesHeatmapView").then((m) => ({ default: m.FuturesHeatmapView })),
);
const FnoWorkspaceView = lazy(() => import("@/features/fno/FnoWorkspaceView").then((m) => ({ default: m.FnoWorkspaceView })));
const OptionChainView = lazy(() => import("@/features/fno/OptionChainView").then((m) => ({ default: m.OptionChainView })));
const SubscriptionsView = lazy(() =>
  import("@/features/subscriptions/SubscriptionsView").then((m) => ({ default: m.SubscriptionsView })),
);
const MarketDataView = lazy(() => import("@/features/market-data/MarketDataView").then((m) => ({ default: m.MarketDataView })));
const InstrumentsView = lazy(() => import("@/features/instruments/InstrumentsView").then((m) => ({ default: m.InstrumentsView })));
const CatalogView = lazy(() => import("@/features/catalog/CatalogView").then((m) => ({ default: m.CatalogView })));
const NewsView = lazy(() => import("@/features/news").then((m) => ({ default: m.NewsView })));
const WatchlistsView = lazy(() => import("@/features/watchlists").then((m) => ({ default: m.WatchlistsView })));
const AlertsView = lazy(() => import("@/features/alerts").then((m) => ({ default: m.AlertsView })));
const AiAlertsView = lazy(() => import("@/features/ai-alerts").then((m) => ({ default: m.AiAlertsView })));
const DiagnosticsView = lazy(() => import("@/features/diagnostics").then((m) => ({ default: m.DiagnosticsView })));
const ChatView = lazy(() => import("@/features/chat").then((m) => ({ default: m.ChatView })));
const SentimentView = lazy(() => import("@/features/sentiment").then((m) => ({ default: m.SentimentView })));
const SettingsView = lazy(() => import("@/features/settings").then((m) => ({ default: m.SettingsView })));

function withFallback(node: ReactNode, label?: string) {
  return <Suspense fallback={<RouteFallback label={label} />}>{node}</Suspense>;
}

export function AppRouter() {
  return (
    <HashRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<DashboardView />} />
          <Route path="/chart" element={withFallback(<ChartView />, "Loading chart…")} />
          <Route path="/scanners" element={withFallback(<ScannersView />)} />
          <Route path="/market-map" element={withFallback(<MarketMapView />)} />
          <Route path="/breadth" element={withFallback(<BreadthView />)} />
          <Route path="/sector-heatmap" element={withFallback(<SectorHeatmapView />)} />
          <Route path="/futures-heatmap" element={withFallback(<FuturesHeatmapView />)} />
          <Route path="/fno" element={withFallback(<FnoWorkspaceView />)} />
          <Route path="/option-chain" element={withFallback(<OptionChainView />)} />
          <Route path="/subscriptions" element={withFallback(<SubscriptionsView />)} />
          <Route path="/market-data" element={withFallback(<MarketDataView />)} />
          <Route path="/instruments" element={withFallback(<InstrumentsView />)} />
          <Route path="/catalog" element={withFallback(<CatalogView />)} />
          <Route path="/news" element={withFallback(<NewsView />)} />
          <Route path="/watchlists" element={withFallback(<WatchlistsView />)} />
          <Route path="/alerts" element={withFallback(<AlertsView />)} />
          <Route path="/ai-alerts" element={withFallback(<AiAlertsView />)} />
          <Route path="/mcp" element={<Navigate to="/settings/ai-mcp" replace />} />
          <Route path="/logs" element={<Navigate to="/settings/logging" replace />} />
          <Route path="/diagnostics" element={withFallback(<DiagnosticsView />)} />
          <Route path="/chat" element={withFallback(<ChatView />)} />
          <Route path="/sentiment" element={withFallback(<SentimentView />)} />
          <Route path="/settings" element={withFallback(<SettingsView />)} />
          <Route path="/settings/:section" element={withFallback(<SettingsView />)} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
