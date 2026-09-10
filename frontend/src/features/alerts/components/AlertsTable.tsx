import { Button } from "@/components/ui";
import { formatCondition } from "../format";
import { useAlertMutations } from "../useAlerts";
import type { MarketAlert } from "../types";

// Renders the alert list with inline enable/rearm/delete controls. All actions
// go through the mutation hook (which invalidates and refetches). No inline
// styles — purely presentational + className.
export function AlertsTable({ alerts }: { alerts: MarketAlert[] }) {
  const m = useAlertMutations();

  if (!alerts.length) {
    return <p className="hint">No alerts configured.</p>;
  }

  return (
    <table className="table alerts-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Condition</th>
          <th>State</th>
          <th>Enabled</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        {alerts.map((a) => (
          <tr key={a.id}>
            <td>{a.tradingsymbol}</td>
            <td className="num">
              {formatCondition(a.field, a.operator, a.threshold)}
            </td>
            <td>
              <span className={`chip ${a.state === "triggered" ? "chip-off" : a.enabled ? "chip-on" : ""}`}>
                {a.state}
              </span>
            </td>
            <td>
              <label className="switch">
                <input
                  type="checkbox"
                  checked={a.enabled}
                  onChange={(e) =>
                    m.setEnabled({ id: a.id, enabled: e.target.checked })
                  }
                />
              </label>
            </td>
            <td>
              <Button
                className="btn-compact"
                onClick={() => m.rearm(a.id)}
                disabled={a.state !== "triggered"}
              >
                Re-arm
              </Button>{" "}
              <Button
                className="btn-compact btn-outline-danger"
                onClick={() => m.remove(a.id)}
              >
                ✕
              </Button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
