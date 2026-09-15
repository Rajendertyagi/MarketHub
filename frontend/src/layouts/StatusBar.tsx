import { useLocation } from "react-router-dom";
import { Icon } from "@/components/Icon";
import { useBackendStatus } from "@/hooks/useBackendStatus";
import { inferredMarketOpen } from "@/utils/market";
import { useAlerts } from "@/features/alerts/useAlerts";
import { labelForPath } from "./nav";

// App-wide thin status strip: live backend connection, inferred market session,
// alert count, current view name, and the API base. Presentational only —
// derives state from hooks/route.
export function StatusBar() {
  const status = useBackendStatus();
  const { pathname } = useLocation();
  const view = labelForPath(pathname) ?? pathname;
  const marketOpen = inferredMarketOpen();
  const { data: alerts } = useAlerts();
  const alertCount = alerts?.alerts?.length ?? 0;
  const apiBase = `${window.location.origin}/api`;

  const statusLabel =
    status === "online"
      ? "Online"
      : status === "offline"
        ? "Offline"
        : "Connecting…";

  return (
    <footer className="app-statusbar">
      <span className={`status-dot status-${status}`} aria-hidden="true" />
      <span className="status-text">{statusLabel}</span>
      <span className="status-sep">·</span>
      <span className={`status-text ${marketOpen ? "pos" : "neg"}`}>
        Market {marketOpen ? "Open" : "Closed"}
        <span className="muted"> (inferred)</span>
      </span>
      <span className="status-sep">·</span>
      <span className="status-item">
        <Icon name="bell" size={12} />
        <span>{alertCount} alerts</span>
      </span>
      <span className="status-spacer" />
      <span className="status-text muted">View: {view}</span>
      <span className="status-sep">·</span>
      <span className="status-text muted">API: {apiBase}</span>
    </footer>
  );
}
