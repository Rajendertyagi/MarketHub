import { HeatmapTile } from "@/components/HeatmapTile";
import type { FuturesHeatTile } from "@/types";
import { changeTileColor, type TileColors } from "@/utils/changeColor";
import { themeColors } from "@/utils/cssVar";
import { fmtInt, fmtPct } from "@/utils/format";
import { coverageLabel, isPriced, oiBasisLabel } from "./coverage";

export type BoardMetric = "price" | "oi";

function fmtNum(v: number | null | undefined): string | null {
  if (v == null) return null;
  return String(Math.round(v * 100) / 100);
}

function fmtOi(v: number | null | undefined): string {
  return v == null ? "—" : fmtInt(Math.round(v));
}

function pct(v: number | null | undefined): string {
  return v == null ? "—" : fmtPct(v);
}

// Native multi-line tooltip (repo convention) — the extra board fields.
function tileTooltip(t: FuturesHeatTile): string {
  return [
    `${t.underlying} · ${t.expiry}`,
    `LTP: ${fmtNum(t.futures_ltp) ?? "—"}`,
    `Price: ${pct(t.price_pct)}`,
    `OI: ${fmtOi(t.oi)}`,
    `Prev OI: ${fmtOi(t.previous_oi)}`,
    `OI chg: ${fmtOi(t.oi_change)}`,
    `OI %: ${pct(t.oi_pct)}`,
    `Buildup: ${t.buildup ?? "—"}`,
    `Spot: ${fmtNum(t.spot_ltp) ?? "—"}`,
    `Basis: ${fmtNum(t.basis) ?? "—"}`,
    `Coverage: ${coverageLabel(t.coverage)}`,
    `OI basis: ${oiBasisLabel(t.oi_basis)}`,
    `Provider: ${t.provider ?? "—"}`,
    `Quote: ${t.received_ts ?? "—"}`,
  ].join("\n");
}

// Uniform card wall over ONE unfiltered board snapshot. Pure presentation:
// no fetching, no broker logic, no board math.
export function FuturesBoardGrid({
  items,
  metric,
  onOpen,
}: {
  items: FuturesHeatTile[];
  metric: BoardMetric;
  onOpen: (tile: FuturesHeatTile) => void;
}) {
  const c = themeColors();
  const colors: TileColors = {
    pos: c.pos,
    neg: c.neg,
    unavailable: c.textFaint,
  };

  return (
    <div className="fboard-grid">
      {items.map((t) => {
        const priced = isPriced(t.coverage);
        const pctVal = metric === "oi" ? t.oi_pct : t.price_pct;
        return (
          <HeatmapTile
            key={t.instrument_key || `${t.underlying}-${t.expiry}`}
            symbol={t.underlying}
            value={fmtNum(t.futures_ltp)}
            change={pct(pctVal)}
            background={priced ? changeTileColor(pctVal, colors) : colors.unavailable}
            color={priced ? c.text : c.textMuted}
            title={tileTooltip(t)}
            badge={priced ? undefined : coverageLabel(t.coverage)}
            dimmed={!priced}
            onClick={() => onOpen(t)}
          />
        );
      })}
    </div>
  );
}
