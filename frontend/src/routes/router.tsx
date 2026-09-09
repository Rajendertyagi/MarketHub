import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/layouts/AppShell";
import { ChartsView } from "@/features/charts/ChartsView";
import { ScannersView } from "@/features/scanners/ScannersView";
import { BreadthView } from "@/features/breadth/BreadthView";
import { SectorHeatmapView } from "@/features/sector-heatmap/SectorHeatmapView";
import { MarketMapView } from "@/features/market-map/MarketMapView";
import { FnoWorkspaceView } from "@/features/fno/FnoWorkspaceView";

export function AppRouter() {
  return (
    <HashRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/charts" replace />} />
          <Route path="/charts" element={<ChartsView />} />
          <Route path="/scanners" element={<ScannersView />} />
          <Route path="/market-map" element={<MarketMapView />} />
          <Route path="/breadth" element={<BreadthView />} />
          <Route path="/sector-heatmap" element={<SectorHeatmapView />} />
          <Route path="/fno" element={<FnoWorkspaceView />} />
          <Route path="*" element={<Navigate to="/charts" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
