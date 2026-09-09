// Reuses the existing analytics-coverage owner for the active universe.
//
// Mirrors the legacy WebUI strategy: request coverage only when the universe
// actually changes (not on every live refresh), and clear it on view unmount.
// This avoids re-POSTing on every poll tick and never duplicates subscription
// ownership client-side.

import { useEffect, useRef } from "react";
import { clearAnalyticsCoverage, ensureAnalyticsCoverage } from "@/api/analytics";

export function useAnalyticsCoverage(universe: string | undefined): void {
  const ensured = useRef<string | null>(null);

  useEffect(() => {
    if (!universe || ensured.current === universe) return;
    ensured.current = universe;
    void ensureAnalyticsCoverage(universe);
  }, [universe]);

  useEffect(() => {
    return () => {
      ensured.current = null;
      void clearAnalyticsCoverage();
    };
  }, []);
}
