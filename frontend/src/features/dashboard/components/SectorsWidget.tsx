import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { getSectorHeatmap } from "@/api/market";
import { fmtInt, fmtNum, tone } from "@/utils/format";
import { WidgetCard } from "./WidgetCard";

// Top sectors by average change %. Mirrors the Sector Analysis page (NIFTY50
// universe) so the card is a faithful entry point to it.
export function SectorsWidget() {
  const q = useQuery({
    queryKey: ["dash-sector-heatmap", "NIFTY50"],
    queryFn: ({ signal }) => getSectorHeatmap("NIFTY50", signal),
    refetchInterval: 8000,
  });

  const top = useMemo(() => {
    if (!q.data) return [];
    return [...q.data.sectors]
      .sort(
        (a, b) => (b.average_change_percent ?? -Infinity) - (a.average_change_percent ?? -Infinity),
      )
      .slice(0, 6);
  }, [q.data]);

  return (
    <WidgetCard
      title="Sectors"
      to="/sector-heatmap"
      subtitle={q.data ? `${q.data.sector_count} sectors` : undefined}
      hint="Open Sector Analysis →"
    >
      {q.isLoading ? (
        <div className="muted">Loading…</div>
      ) : top.length === 0 ? (
        <div className="muted">No sector data</div>
      ) : (
        <ul className="widget-list">
          {top.map((s) => (
            <li key={s.sector} className="widget-row">
              <span className="w-sym">{s.sector}</span>
              <span className="w-meta muted">
                {fmtInt(s.advances)}/{fmtInt(s.declines)}
              </span>
              <span className={`w-chg num ${tone(s.average_change_percent)}`}>
                {s.average_change_percent != null ? `${fmtNum(s.average_change_percent)}%` : "—"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </WidgetCard>
  );
}
