// Formatting helpers for MarketHub presentation.
// Backend canonical values are never mutated here — only formatted for display.

export function fmtNum(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtInt(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return Math.round(value).toLocaleString();
}

export function fmtVol(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  const abs = Math.abs(value);
  if (abs >= 1e9) return (value / 1e9).toFixed(2) + "B";
  if (abs >= 1e7) return (value / 1e7).toFixed(2) + "Cr";
  if (abs >= 1e5) return (value / 1e5).toFixed(2) + "L";
  if (abs >= 1e3) return (value / 1e3).toFixed(2) + "K";
  return String(Math.round(value));
}

export function fmtPct(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  const cls = value > 0 ? "+" : "";
  return `${cls}${value.toFixed(digits)}%`;
}

export function fmtSigned(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  const cls = value > 0 ? "+" : "";
  return `${cls}${fmtNum(value)}`;
}

// Canonical IV is a DECIMAL FRACTION (0.1758 == 17.58%). React only FORMATS it as
// a percentage — it must never divide/multiply the canonical value, treat >1 as
// invalid, or coerce null to zero. 0 stays 0%; null stays unavailable.
export function fmtIv(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${(value * 100).toFixed(2)}%`;
}

// Sign tone for change-style values: positive → "pos", negative → "neg", else "".
export function tone(value: number | null | undefined): "" | "pos" | "neg" {
  if (value == null || Number.isNaN(value)) return "";
  if (value > 0) return "pos";
  if (value < 0) return "neg";
  return "";
}
