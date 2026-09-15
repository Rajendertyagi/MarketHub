import { AsyncStateView } from "@/components/ui";
import { ApiError } from "@/types";
import { shortId } from "../constants";
import { deliveryBadgeClass, deliveryLabel, timeAgo } from "../format";
import { useTriggeredEvents } from "../useAiAlerts";

// Durable triggered events with delivery/pending/ACK status. Read-only.
export function EventsTable() {
  const { data, status, error, refetch } = useTriggeredEvents();

  if (status === "pending") {
    return <AsyncStateView status="loading" loadingLabel="Loading events…" />;
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

  const events = data?.events ?? [];
  if (!events.length) {
    return <p className="hint">No triggered events yet.</p>;
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Triggered events</h2>
        <span className="muted">{events.length} events</span>
      </div>
      <div className="table-scroll">
        <table className="table table-compact">
          <thead>
            <tr>
              <th>Event</th>
              <th>Alert</th>
              <th>Consumer</th>
              <th>Instrument</th>
              <th>Condition</th>
              <th>Delivery</th>
              <th>Trigger</th>
              <th>ACK</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={String(e.event_id)}>
                <td className="mono" title={String(e.event_id)}>
                  {shortId(e.event_id)}
                </td>
                <td className="mono" title={e.alert_id}>
                  {shortId(e.alert_id)}
                </td>
                <td>{e.consumer_id}</td>
                <td>{e.instrument || "—"}</td>
                <td className="ai-event-condition" title={e.condition_summary}>
                  {e.condition_summary}
                </td>
                <td>
                  <span className={deliveryBadgeClass(e.delivery_state)}>
                    {deliveryLabel(e.delivery_state)}
                  </span>
                </td>
                <td>{timeAgo(e.trigger_time)}</td>
                <td>{e.acknowledged_at ? timeAgo(e.acknowledged_at) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
