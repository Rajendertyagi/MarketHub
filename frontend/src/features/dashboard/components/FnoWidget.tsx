import { useQuery } from "@tanstack/react-query";
import { getFnoUniverse } from "@/api/market";
import { fmtInt } from "@/utils/format";
import { WidgetCard } from "./WidgetCard";

// F&O universe snapshot: total covered stocks plus a few representatives with
// their futures/options availability. Entry point to the F&O workspace.
export function FnoWidget() {
  const q = useQuery({
    queryKey: ["dash-fno-universe"],
    queryFn: ({ signal }) => getFnoUniverse("", 12, signal),
    refetchInterval: 15000,
  });

  const rows = q.data?.universe.slice(0, 8) ?? [];

  return (
    <WidgetCard
      title="F&O"
      to="/fno"
      subtitle={q.data ? `${fmtInt(q.data.count)} stocks` : undefined}
      hint="Open F&O →"
    >
      {q.isLoading ? (
        <div className="muted">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="muted">No F&O data</div>
      ) : (
        <ul className="widget-list">
          {rows.map((r) => (
            <li key={r.symbol} className="widget-row">
              <span className="w-sym">{r.symbol}</span>
              <span className="w-meta muted">
                {r.futures_available ? "F" : "·"}
                {r.options_available ? "O" : "·"}
              </span>
              <span className="w-chg muted">
                {fmtInt(r.futures_count)}F / {fmtInt(r.options_count)}O
              </span>
            </li>
          ))}
        </ul>
      )}
    </WidgetCard>
  );
}
