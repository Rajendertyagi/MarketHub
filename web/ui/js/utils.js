/**
 * MarketHub WebUI — shared DOM/escaping/format helpers.
 *
 * Plain ES module, no framework. Imported by feature modules so escaping
 * and formatting stay consistent (and untrusted source metadata can never
 * reach innerHTML unescaped).
 */

export const $ = (id) => document.getElementById(id);

export function esc(s) {
  return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export function escAttr(s) {
  return esc(s).replace(/'/g, "&#39;");
}

export function fmtLogTs(ts) {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString("en-GB", { hour12: false }) + "." +
           String(d.getMilliseconds()).padStart(3, "0");
  } catch { return ts; }
}

/**
 * Shared number/change/indicator formatting for market rendering.
 * Moved verbatim from the terminal core so every market surface
 * formats identically.
 */

export const fmt = (v, dp = 2) =>
  v != null ? Number(v).toLocaleString("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp }) : "—";

export const fmtVol = (v) => {
  if (v == null) return "—";
  if (v >= 1e7) return (v / 1e7).toFixed(2) + "Cr";
  if (v >= 1e5) return (v / 1e5).toFixed(2) + "L";
  if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return String(v);
};

export const chgClass = (v) => v > 0 ? "up" : v < 0 ? "down" : "";

export const nowStr = () => new Date().toLocaleTimeString();

export function setIndicator(id, on, text) {
  const el = $(id);
  el.className = "indicator " + (on ? "indicator-on" : "indicator-off");
  el.textContent = text;
}

/**
 * HTML escaper with "—" fallback for nullish values (used across market,
 * source, instrument, watchlist, chart and alert rendering).
 */
export const escDash = (v) => String(v ?? "—")
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

export const fmtNum = (v) => v != null ? Number(v).toLocaleString("en-IN") : "—";

export const fmtTs = (iso) => {
  try { return new Date(iso).toLocaleTimeString(); } catch { return "—"; }
};
