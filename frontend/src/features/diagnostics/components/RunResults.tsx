import { statusKind } from "../constants";
import type { DiagnosticRunResult } from "../types";

// Renders a completed diagnostic run: summary counts + per-check result table.
export function RunResults({ result }: { result: DiagnosticRunResult }) {
  const statuses = Object.entries(result.summary).sort((a, b) => b[1] - a[1]);

  return (
    <div className="card diag-results">
      <div className="card-header">
        <h2>Run results</h2>
        <span className="muted">
          {result.symbol ?? "—"} · {result.duration_ms} ms ·{" "}
          {new Date(
            result.run_at.includes("T")
              ? result.run_at
              : result.run_at.replace(" ", "T"),
          ).toLocaleString()}
        </span>
      </div>

      <div className="diag-summary">
        {statuses.map(([status, count]) => (
          <span
            key={status}
            className={`ui-badge ui-badge-${statusKind(status)}`}
          >
            {status}: {count}
          </span>
        ))}
      </div>

      <table className="table table-compact">
        <thead>
          <tr>
            <th>Check</th>
            <th>Category</th>
            <th>Layer</th>
            <th>Status</th>
            <th>Message</th>
            <th className="num">ms</th>
          </tr>
        </thead>
        <tbody>
          {result.results.map((r) => (
            <tr
              key={r.id}
              className={
                r.status === "FAIL"
                  ? "diag-row diag-row-fail"
                  : r.status === "PASS"
                    ? "diag-row diag-row-pass"
                    : "diag-row"
              }
            >
              <td>{r.name}</td>
              <td>{r.category}</td>
              <td>{r.layer}</td>
              <td>
                <span className={`ui-badge ui-badge-${statusKind(r.status)}`}>
                  {r.status}
                </span>
              </td>
              <td className="diag-msg">{r.message}</td>
              <td className="num">{r.duration_ms}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
