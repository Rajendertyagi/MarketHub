import { AsyncStateView } from "@/components/ui";
import type { ApiError } from "@/types";
import { shortId } from "../constants";
import { enabledLabel, timeAgo } from "../format";
import { useConditionAlerts } from "../useAiAlerts";

// Active condition alerts with runtime state. Read-only.
export function AlertsTable() {
  const { data, status, error, refetch } = useConditionAlerts();

  if (status === "pending") {
    return <AsyncStateView status="loading" loadingLabel="Loading alerts…" />;
  }
  if (status === "error") {
    return <AsyncStateView status="error" error={error as ApiError} onRetry={() => refetch()} />;
  }

  const alerts = data?.alerts ?? [];
  if (!alerts.length) {
    return <p className="hint">No condition alerts.</p>;
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Condition alerts</h2>
        <span className="muted">{alerts.length} alerts</span>
      </div>
      <div className="table-scroll">
        <table className="table table-compact">
          <thead>
            <tr>
              <th>Alert</th>
              <th>Consumer</th>
              <th>Instrument</th>
              <th>Condition</th>
              <th>Mode</th>
              <th>State</th>
              <th>Enabled</th>
              <th className="num">Count</th>
              <th>Last trigger</th>
            </tr>
          </thead>
          <tbody>
            {alerts.map((a) => (
              <tr key={a.alert_id}>
                <td className="mono" title={a.alert_id}>
                  {shortId(a.alert_id)}
                </td>
                <td>{a.consumer_id}</td>
                <td>{a.instrument ?? "—"}</td>
                <td className="ai-alert-condition" title={a.condition_summary}>
                  {a.condition_summary}
                </td>
                <td>
                  <span className="ui-badge ui-badge-info">{a.trigger_mode}</span>
                </td>
                <td>
                  <span
                    className={`ui-badge ${
                      a.current_state === "triggered" ? "ui-badge-warning" : "ui-badge-neutral"
                    }`}
                  >
                    {a.current_state}
                  </span>
                </td>
                <td>
                  <span
                    className={`ui-badge ${a.enabled ? "ui-badge-success" : "ui-badge-neutral"}`}
                  >
                    {enabledLabel(a.enabled)}
                  </span>
                </td>
                <td className="num">{a.trigger_count}</td>
                <td>{timeAgo(a.last_triggered_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
