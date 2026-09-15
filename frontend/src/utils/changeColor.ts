// Presentation-only color scale for market-movement tiles.
//
// Maps a backend Change % to a green(up)/red(down) color, saturating at +/-5%.
// This is the SAME continuous scale used across Breadth / Sector Heatmap /
// Market Map — it represents the backend change value only and never introduces
// a second semantic scale. It is pure (no canonical computation).

export const CHANGE_CAP = 5;

export interface TileColors {
  pos: string;
  neg: string;
  unavailable: string;
}

// Concrete colors are supplied by the caller (resolved from theme tokens) so the
// function stays pure and testable. `null`/undefined change -> unavailable gray.
export function changeTileColor(
  pct: number | null | undefined,
  colors: TileColors,
): string {
  if (pct === null || pct === undefined) return colors.unavailable;
  const mag = Math.max(-1, Math.min(1, pct / CHANGE_CAP));
  if (mag >= 0) {
    const l = 62 - mag * 32;
    return `hsl(140, 55%, ${l}%)`;
  }
  const l = 62 + mag * 32;
  return `hsl(0, 60%, ${l}%)`;
}

// Sequential "activity" scale for the Volume heatmap metric: faint -> saturated
// cool hue as normalized volume t (0..1) increases. Independent of the
// green/red change scale so the two metrics read distinctly.
export function volumeTileColor(t: number | null | undefined): string {
  if (t === null || t === undefined) return "hsl(195, 30%, 32%)";
  const clamped = Math.max(0, Math.min(1, t));
  const l = 62 - clamped * 36; // 62% -> 26%
  return `hsl(195, 70%, ${l}%)`;
}
