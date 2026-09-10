import { AsyncStateView } from "@/components/ui";
import { ApiError } from "@/types";
import { useDiagnosticsChecks } from "../useDiagnostics";

// Reference list of available diagnostic checks (read-only metadata).
export function ChecksList() {
  const { data, status, error, refetch } = useDiagnosticsChecks();

  if (status === "pending") {
    return <AsyncStateView status="loading" loadingLabel="Loading checks…" />;
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

  const checks = data?.checks ?? [];
  return (
    <div className="card">
      <div className="card-header">
        <h2>Available checks</h2>
        <span className="muted">{checks.length} checks</span>
      </div>
      <ul className="diag-checks">
        {checks.map((c) => (
          <li key={c.id}>
            <span className="diag-check-name">{c.name}</span>
            <span className="muted">
              {c.category} · {c.layer} · {c.mode.join("/")}
            </span>
            <span className="diag-check-desc">{c.description}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
