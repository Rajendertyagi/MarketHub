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
