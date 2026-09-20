import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { runScanner } from "@/api/market";
import type { ScanRow } from "@/types";
import { fmtPct, fmtVol, tone } from "@/utils/format";
import { MOVER_CATEGORIES, type MoverCategory } from "../constants";
import { WidgetCard } from "./WidgetCard";

// Dashboard movers: top gainers / losers / volume leaders from the canonical
// scanner service (backend names: gainers / losers / volume, FNO universe).
// Entry point to the full Scanners page. Rows stay non-interactive — the
// whole WidgetCard is the affordance into /scanners.
const SCANNER_BY_CATEGORY: Record<MoverCategory, string> = {
  "Top Gainer": "gainers",
  "Top Loser": "losers",
  "Volume Leader": "volume",
};

export function MoversWidget() {
  const [active, setActive] = useState<MoverCategory>(MOVER_CATEGORIES[0]);

  const q = useQuery({
    queryKey: ["dash-movers", SCANNER_BY_CATEGORY[active]],
    queryFn: ({ signal }) =>
      runScanner(SCANNER_BY_CATEGORY[active], { universe: "FNO", limit: 5 }, signal),
    refetchInterval: 15000,
  });

  const rows = q.data?.rows.slice(0, 5) ?? [];

  return (
    <WidgetCard title="Movers" to="/scanners" subtitle={active} hint="Open Scanners →">
      <div className="movers-tabs" role="tablist" aria-label="Mover category">
        {MOVER_CATEGORIES.map((c) => (
          <button
            key={c}
            type="button"
            role="tab"
            aria-selected={c === active}
            className={`movers-tab${c === active ? " active" : ""}`}
            onClick={(e) => {
              e.stopPropagation();
              setActive(c);
            }}
            onKeyDown={(e) => {
              // Don't let the parent WidgetCard's Enter/Space navigation fire
              // when a tab itself is activated.
              e.stopPropagation();
            }}
          >
            {c}
          </button>
        ))}
      </div>
      {q.isLoading ? (
        <div className="muted">Loading…</div>
      ) : q.isError || rows.length === 0 ? (
        <div className="muted">No movers data</div>
      ) : (
        <ul className="widget-list">
          {rows.map((r) => (
            <MoverRow key={r.symbol} row={r} category={active} />
          ))}
        </ul>
      )}
    </WidgetCard>
  );
}

function MoverRow({ row, category }: { row: ScanRow; category: MoverCategory }) {
  if (category === "Volume Leader") {
    return (
      <li className="widget-row">
        <span className="w-sym">{row.symbol}</span>
        <span className="w-meta muted">{row.sector}</span>
        <span className="w-chg muted">{fmtVol(row.volume)}</span>
      </li>
    );
  }
  const t = tone(row.change_percent);
  return (
    <li className="widget-row">
      <span className="w-sym">{row.symbol}</span>
      <span className="w-meta muted">{row.sector}</span>
      <span className={`w-chg ${t}`}>{fmtPct(row.change_percent)}</span>
    </li>
  );
}
