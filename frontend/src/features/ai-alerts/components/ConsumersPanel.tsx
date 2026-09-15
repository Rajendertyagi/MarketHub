import { AsyncStateView } from "@/components/ui";
import { ApiError } from "@/types";
import { shortId } from "../constants";
import { timeAgo } from "../format";
import { useConsumers } from "../useAiAlerts";

// Per-consumer delivery status cards. Read-only observability over durable state.
export function ConsumersPanel() {
  const { data, status, error, refetch } = useConsumers();

  if (status === "pending") {
    return <AsyncStateView status="loading" loadingLabel="Loading consumers…" />;
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

  const consumers = data?.consumers ?? [];
  if (!consumers.length) {
    return <p className="hint">No consumers registered.</p>;
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Consumers</h2>
        <span className="muted">{consumers.length} consumers</span>
      </div>
      <div className="ai-consumers-grid">
        {consumers.map((c) => (
          <div className="ai-consumer-card" key={c.consumer_id}>
          <div className="ai-consumer-id" title={c.consumer_id}>
            {c.consumer_id}
          </div>
          <div className="ai-consumer-meta">
            <div>
              Pending:{" "}
              <strong
                className={c.pending_count > 0 ? "ai-consumer-pending" : ""}
              >
                {c.pending_count}
              </strong>
            </div>
            <div>Unacked: <strong>{c.unacknowledged_count}</strong></div>
            <div>
              Last trigger:{" "}
              {c.last_triggered
                ? timeAgo(c.last_triggered.trigger_time)
                : "—"}
            </div>
            <div>
              Checkpoint:{" "}
              {c.last_checkpoint ? `#${c.last_checkpoint.last_sequence}` : "—"}
            </div>
          </div>
          <div className="ai-consumer-foot muted">
            id {shortId(c.consumer_id)}
          </div>
        </div>
      ))}
      </div>
    </div>
  );
}
