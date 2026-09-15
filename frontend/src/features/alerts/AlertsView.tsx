import { ApiError } from "@/types";
import { AsyncStateView } from "@/components/ui";
import { useAlerts } from "./useAlerts";
import { AlertForm } from "./components/AlertForm";
import { AlertsTable } from "./components/AlertsTable";
import { NotificationsPanel } from "./components/NotificationsPanel";
import { AlertHistory } from "./components/AlertHistory";

export function AlertsView() {
  const { data, status, error, refetch } = useAlerts();

  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">Price Alerts</h1>
        <span className="muted">
          Server-evaluated thresholds — React only configures &amp; displays
        </span>
      </div>

      <AlertForm />

      {status === "pending" ? (
        <AsyncStateView status="loading" loadingLabel="Loading alerts…" />
      ) : status === "error" ? (
        <AsyncStateView
          status="error"
          error={error as ApiError}
          onRetry={() => refetch()}
        />
      ) : (
        <div className="panel">
          <div className="panel-header">
            <h2>Configured alerts</h2>
            <span className="muted">{data?.alerts?.length ?? 0} alerts</span>
          </div>
          <AlertsTable alerts={data?.alerts ?? []} />
        </div>
      )}

      {status === "success" ? (
        <NotificationsPanel notifications={data?.notifications ?? []} />
      ) : null}

      <AlertHistory />
    </div>
  );
}
