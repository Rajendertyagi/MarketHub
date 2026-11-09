import type { ReactNode } from "react";

// Reusable uniform heatmap tile (CSS-grid based). Presentation only — no data
// semantics, no broker knowledge. Extensible via `extra` so cards can gain
// more info later without changing the layout.
export interface HeatmapTileProps {
  symbol: string;
  /** Formatted LTP (or any primary value). */
  value?: string | null;
  /** Formatted change % (or the active metric's %). */
  change?: string | null;
  background?: string;
  color?: string;
  /** Native tooltip (repo convention) — may be multi-line. */
  title?: string;
  badge?: string;
  dimmed?: boolean;
  extra?: ReactNode;
  onClick?: () => void;
}

export function HeatmapTile({
  symbol,
  value,
  change,
  background,
  color,
  title,
  badge,
  dimmed,
  extra,
  onClick,
}: HeatmapTileProps) {
  return (
    <button
      type="button"
      className={dimmed ? "tile tile-dim" : "tile"}
      style={{ background, color }}
      title={title}
      onClick={onClick}
    >
      <span className="tile-sym">{symbol}</span>
      {value != null && <span className="tile-val">{value}</span>}
      {change != null && <span className="tile-chg">{change}</span>}
      {badge && <span className="tile-badge">{badge}</span>}
      {extra}
    </button>
  );
}
