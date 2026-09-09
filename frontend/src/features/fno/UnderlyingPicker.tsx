import { useMemo, useState } from "react";
import type { CombinedUnderlying } from "./useFno";
import { Input } from "@/components/ui";
import { fmtInt } from "@/utils/format";

interface Props {
  underlyings: CombinedUnderlying[];
  isLoading: boolean;
  onSelect: (symbol: string, kind: CombinedUnderlying["kind"]) => void;
}

// Unified F&O underlying picker: equity F&O stocks (derived catalog) + optionable
// index underlyings. Selection preserves the exact canonical symbol and its kind
// (equity vs index) so the workspace can pick the right canonical data path.
export function UnderlyingPicker({ underlyings, isLoading, onSelect }: Props) {
  const [q, setQ] = useState("");

  const filtered = useMemo(() => {
    const t = q.trim().toUpperCase();
    if (!t) return underlyings;
    return underlyings.filter(
      (u) =>
        u.symbol.includes(t) ||
        (u.name ?? "").toUpperCase().includes(t),
    );
  }, [underlyings, q]);

  const equityCount = underlyings.filter((u) => u.kind === "equity").length;
  const indexCount = underlyings.filter((u) => u.kind === "index").length;

  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">F&O Workspace</h1>
        <span className="muted">
          {fmtInt(equityCount)} equity · {fmtInt(indexCount)} index underlyings
        </span>
      </div>

      <div className="control-row">
        <Input
          placeholder="Search underlying…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ width: 280 }}
        />
      </div>

      {isLoading ? (
        <div className="state">
          <div className="spinner" />
          <span className="muted">Loading underlyings…</span>
        </div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: "auto", maxHeight: 520 }}>
          <table className="table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Name</th>
                <th>Type</th>
                <th>Futures</th>
                <th>Options</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {filtered.map((u) => (
                <tr key={`${u.kind}:${u.symbol}`}>
                  <td>{u.symbol}</td>
                  <td className="muted">{u.name ?? "—"}</td>
                  <td>
                    <span className={u.kind === "index" ? "chip chip-info" : "chip"}>
                      {u.kind === "index" ? "Index" : "Equity"}
                    </span>
                  </td>
                  <td className="muted">
                    {u.futures_available == null
                      ? "—"
                      : u.futures_available
                        ? "✓"
                        : "—"}
                  </td>
                  <td className="muted">
                    {u.options_available == null
                      ? "—"
                      : u.options_available
                        ? "✓"
                        : "—"}
                  </td>
                  <td>
                    <button
                      className="btn btn-compact"
                      onClick={() => onSelect(u.symbol, u.kind)}
                    >
                      Open
                    </button>
                  </td>
                </tr>
              ))}
              {!filtered.length && (
                <tr>
                  <td colSpan={6} className="empty-row">
                    No underlyings match.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
