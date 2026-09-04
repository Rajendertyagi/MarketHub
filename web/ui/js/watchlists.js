/**
 * MarketHub WebUI — watchlists.
 *
 * Owns watchlist selection/CRUD/item rendering with live values resolved
 * from the market quote universe (market.js). The 10s refresh timer is
 * created exactly once.
 */

import { $, chgClass, escDash, fmt, fmtVol } from "./utils.js";
import { getQuote } from "./market.js";

let currentWatchlistId = null;
let _watchlistsInitDone = false;

export async function loadWatchlists() {
  try {
    const res = await fetch("/api/watchlists");
    const data = await res.json();
    const sel = $("wl-select");
    if (sel) sel.innerHTML = (data.watchlists || []).map((w) =>
      `<option value="${w.id}">${escDash(w.name)}</option>`).join("");
    currentWatchlistId = sel && sel.value ? Number(sel.value) : null;
    renderWatchlistItems(data.watchlists || []);
  } catch { /* silent */ }
}

function renderWatchlistItems(watchlists) {
  const wl = (watchlists || []).find(
    (w) => String(w.id) === String(currentWatchlistId));
  const body = $("wl-body");
  if (!wl || !wl.items.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty-row">No items. Add instruments from the Instruments page.</td></tr>';
    return;
  }
  body.innerHTML = wl.items.map((it) => {
    const q = getQuote(it.instrument_token)
      || getQuote(it.exchange + ":" + it.instrument_token);
    const ltp = q && q.ltp != null ? fmt(q.ltp) : "—";
    const chg = q && q.change != null ? fmt(q.change) : "—";
    const pct = q && q.change_percent != null
      ? fmt(q.change_percent) + "%" : "—";
    const cls = q ? chgClass(q.change ?? 0) : "";
    return `<tr data-item-id="${it.id}" data-token="${it.instrument_token}" data-ex="${escDash(it.exchange)}">` +
      `<td>${escDash(it.tradingsymbol)}</td><td>${ltp}</td>` +
      `<td class="${cls}">${chg}</td><td class="${cls}">${pct}</td>` +
      `<td>${q ? fmtVol(q.volume) : "—"}</td>` +
      `<td>${q && q.best_bid != null ? fmt(q.best_bid) : "—"}</td>` +
      `<td>${q && q.best_ask != null ? fmt(q.best_ask) : "—"}</td>` +
      `<td><button class="btn wl-remove" style="padding:2px 8px;font-size:11px">✕</button></td></tr>`;
  }).join("");
}

export function initWatchlists() {
  if (_watchlistsInitDone) return;
  _watchlistsInitDone = true;
  $("wl-create").addEventListener("click", async () => {
    const name = $("wl-new-name").value.trim();
    if (!name) return;
    await fetch("/api/watchlists", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }) });
    $("wl-new-name").value = "";
    loadWatchlists();
  });
  $("wl-rename").addEventListener("click", async () => {
    const name = $("wl-new-name").value.trim();
    if (!name || !currentWatchlistId) return;
    await fetch(`/api/watchlists/${currentWatchlistId}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }) });
    $("wl-new-name").value = "";
    loadWatchlists();
  });
  $("wl-delete").addEventListener("click", async () => {
    if (!currentWatchlistId ||
        !confirm("Delete this watchlist and its items?")) return;
    await fetch(`/api/watchlists/${currentWatchlistId}`,
      { method: "DELETE" });
    loadWatchlists();
  });
  $("wl-select").addEventListener("change", loadWatchlists);
  $("wl-table").addEventListener("click", async (e) => {
    const btn = e.target.closest(".wl-remove");
    if (!btn) return;
    const itemId = btn.closest("tr").dataset.itemId;
    await fetch(`/api/watchlists/items/${itemId}`, { method: "DELETE" });
    loadWatchlists();
  });
  setInterval(loadWatchlists, 10000);   // refresh live values from SSE state
  loadWatchlists();
}
