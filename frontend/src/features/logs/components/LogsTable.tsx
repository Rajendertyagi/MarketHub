import { forwardRef } from "react";
import type { LogRecord } from "../types";

function formatTs(ts: string): string {
  const d = new Date(ts.includes("T") ? ts : ts.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString();
}

// Renders the combined (live + history) log records. Newest first. The parent
// owns scroll/auto-follow via the forwarded ref on the scroll container.
export const LogsTable = forwardRef<HTMLDivElement, { records: LogRecord[] }>(
  function LogsTable({ records }, ref) {
    if (!records.length) {
      return (
        <div className="logs-container" ref={ref}>
          <p className="hint">No log records.</p>
        </div>
      );
    }
    return (
      <div className="logs-container" ref={ref}>
        <table className="table logs-table table-compact">
          <thead>
            <tr>
              <th>Time</th>
              <th>Level</th>
              <th>Component</th>
              <th>Message</th>
            </tr>
          </thead>
          <tbody>
            {records.map((r, i) => (
              <tr key={`${r.ts}-${i}`} className={`logs-row logs-level-${r.level.toLowerCase()}`}>
                <td className="mono logs-ts num">{formatTs(r.ts)}</td>
                <td className={`logs-lvl-${r.level.toLowerCase()}`}>{r.level}</td>
                <td className="logs-component">{r.logger}</td>
                <td className="logs-message">
                  {r.message}
                  {r.exception ? (
                    <span className="logs-exception">
                      {" ["}
                      {r.exception.slice(0, 200)}
                      {"]"}
                    </span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  },
);
