import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/layouts/AppShell";
import { ChartsView } from "@/features/charts/ChartsView";
import { ScannersView } from "@/features/scanners/ScannersView";

export function AppRouter() {
  return (
    <HashRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/charts" replace />} />
          <Route path="/charts" element={<ChartsView />} />
          <Route path="/scanners" element={<ScannersView />} />
          <Route path="*" element={<Navigate to="/charts" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}
