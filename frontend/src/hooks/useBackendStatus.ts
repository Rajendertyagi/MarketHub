import { useEffect, useState } from "react";

export type BackendStatus = "online" | "offline" | "checking";

// Lightweight connectivity probe for the status bar. Polls an existing cheap
// endpoint (/api/diagnostics) on an interval; no new backend route required.
export function useBackendStatus(intervalMs = 30000): BackendStatus {
  const [status, setStatus] = useState<BackendStatus>("checking");

  useEffect(() => {
    let alive = true;
    const check = async () => {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 5000);
      try {
        const res = await fetch("/api/diagnostics", {
          signal: ctrl.signal,
          headers: { Accept: "application/json" },
        });
        if (alive) setStatus(res.ok ? "online" : "offline");
      } catch {
        if (alive) setStatus("offline");
      } finally {
        clearTimeout(timer);
      }
    };
    check();
    const id = setInterval(check, intervalMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [intervalMs]);

  return status;
}
