import { formatCondition } from "../format";
import type { AlertNotification } from "../types";

// Live (in-memory) trigger notifications from the alert engine. These are not
// durable; the durable record lives in the trigger-history table below.
export function NotificationsPanel({
  notifications,
}: {
  notifications: AlertNotification[];
}) {
  if (!notifications.length) {
    return (
      <div className="panel">
        <div className="panel-header">
          <h2>Triggered</h2>
        </div>
        <p className="hint">No recent triggers.</p>
      </div>
    );
  }
  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Triggered</h2>
      </div>
      <ul className="notifications-list">
        {notifications.map((n, i) => (
          <li key={`${n.alert_id}-${i}`} className="hint err notification-item">
            {n.tradingsymbol}: {formatCondition(n.field, n.operator, n.threshold)}{" "}
            (now {n.value ?? "—"})
          </li>
        ))}
      </ul>
    </div>
  );
}
