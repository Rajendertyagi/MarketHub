import { useQuery } from "@tanstack/react-query";
import { getBreadth } from "@/api/market";
import { WidgetCard } from "./WidgetCard";
import { fmtInt, fmtPct } from "@/utils/format";

// Market breadth at a glance: an advance/decline split bar plus headline counts.
// Entry point to the full Breadth page (NIFTY50 universe).
export function BreadthWidget() {
  const q = useQuery({
    queryKey: ["dash-breadth", "NIFTY50"],
    queryFn: ({ signal }) => getBreadth("NIFTY50", signal),
    refetchInterval: 8000,
  });

  const d = q.data;
  const advPct = d?.advance_percent ?? 0;

  return (
    <WidgetCard
      title="Breadth"
      to="/breadth"
      subtitle={d?.universe}
      hint="Open Breadth →"
    >
      {q.isLoading ? (
        <div className="muted">Loading…</div>
      ) : !d ? (
        <div className="muted">No breadth data</div>
      ) : (
        <div className="breadth-mini">
          <div className="breadth-bar" aria-hidden="true">
            <span className="adv" style={{ width: `${advPct}%` }} />
            <span className="dec" style={{ width: `${100 - advPct}%` }} />
          </div>
          <div className="breadth-stats">
            <span className="pos">{fmtInt(d.advances)} ▲</span>
            <span className="neg">{fmtInt(d.declines)} ▼</span>
            <span className="muted">{fmtInt(d.unchanged)} =</span>
            <span className="muted">{fmtPct(d.advance_percent)} adv</span>
          </div>
        </div>
      )}
    </WidgetCard>
  );
}
