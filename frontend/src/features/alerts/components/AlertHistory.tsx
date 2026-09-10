import { Button } from "@/components/ui";
import { AsyncStateView } from "@/components/ui";
import { ApiError } from "@/types";
import { formatCondition, formatTimestamp } from "../format";
import { useAlertHistory, useAlertMutations } from "../useAlerts";

// Durable, restart-safe record of every individual alert firing. Bounded and
// paginated server-side; the frontend only renders + offers a clear action.
export function AlertHistory() {
  const { data, status, error, refetch } = useAlertHistory();
  const m = useAlertMutations();

  if (status === "pending") {
    return <AsyncStateView status="loading" loadingLabel="Loading history…" />;
  }
  if (status === "error") {
    return (
      <AsyncStateView
        status="error"
        error={error as ApiError}
        onRetry={() => refetch()}
      />
    );
  }

  const rows = data?.history ?? [];

  return (
    <div className="card">
      <div className="card-header alert-history-head">
        <h2>Trigger history</h2>
        <Button
          className="btn-compact"
          onClick={() => {
            if (
              window.confirm(
                "Clear all alert trigger history? This cannot be undone.",
              )
            ) {
              void m.clearHistory();
            }
          }}
        >
          Clear history
        </Button>
      </div>
      {rows.length === 0 ? (
        <p className="hint">No trigger history yet.</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>When</th>
              <th>Symbol</th>
              <th>Condition</th>
              <th>Observed</th>
              <th>Provider</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((h) => (
              <tr key={h.id}>
                <td>{formatTimestamp(h.triggered_at)}</td>
                <td>{h.tradingsymbol ?? "—"}</td>
                <td>
                  {formatCondition(h.field, h.operator, h.threshold ?? 0)}
                </td>
                <td className="num">{h.observed_value ?? "—"}</td>
                <td>{h.provider ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
