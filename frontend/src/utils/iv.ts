// Canonical IV semantics.
//
// Backend canonical IV is a DECIMAL FRACTION: 0.1758 == 17.58%.
// We never change backend semantics. Only presentation converts the fraction to
// a percentage. No magnitude heuristics: 0 is valid, values > 1.0 are valid.

export function ivToPercent(iv: number | null | undefined): string {
  if (iv === null || iv === undefined || Number.isNaN(iv)) return "-";
  return (iv * 100).toFixed(2) + "%";
}
