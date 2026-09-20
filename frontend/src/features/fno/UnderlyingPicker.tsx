import { useMemo, useState } from "react";
import { Input } from "@/components/ui";
import { fmtInt } from "@/utils/format";
import type { CombinedUnderlying } from "./useFno";

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
      (u) => u.symbol.includes(t) || (u.name ?? "").toUpperCase().includes(t),
    );
  }, [underlyings, q]);

  const equityCount = underlyings.filter((u) => u.kind === "equity").length;
  const indexCount = underlyings.filter((u) => u.kind === "index").length;

  return (
    <div className="fno-picker">
      <div className="fno-picker-top">
        <h2 className="fno-picker-title">F&amp;O Underlyings</h2>
        <span className="muted fno-picker-count">
          {fmtInt(equityCount)} equity · {fmtInt(indexCount)} index
        </span>
        <Input
          placeholder="Search underlying…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="fno-picker-search"
        />
      </div>

      {isLoading ? (
        <div className="state">
          <div className="spinner" />
          <span className="muted">Loading underlyings…</span>
        </div>
      ) : (
        <div className="fno-picker-list">
          <table className="table fno-picker-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Name</th>
                <th>Type</th>
                <th className="num">Futures</th>
                <th className="num">Options</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((u) => (
                <tr
                  key={`${u.kind}:${u.symbol}`}
                  className="fno-row"
                  onClick={() => onSelect(u.symbol, u.kind)}
                >
                  <td className="fno-row-sym">
                    <button
                      type="button"
                      className="fno-row-select"
                      aria-label={`Select ${u.symbol}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelect(u.symbol, u.kind);
                      }}
                    >
                      {u.symbol}
                    </button>
                  </td>
                  <td className="muted">{u.name ?? "—"}</td>
                  <td>
                    <span className={u.kind === "index" ? "chip chip-info" : "chip"}>
                      {u.kind === "index" ? "Index" : "Equity"}
                    </span>
                  </td>
                  <td className="num muted">
                    {u.futures_available == null ? "—" : u.futures_available ? "✓" : "—"}
                  </td>
                  <td className="num muted">
                    {u.options_available == null ? "—" : u.options_available ? "✓" : "—"}
                  </td>
                </tr>
              ))}
              {!filtered.length && (
                <tr>
                  <td colSpan={5} className="empty-row">
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
