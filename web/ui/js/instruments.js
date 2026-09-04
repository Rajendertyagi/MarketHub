/**
 * MarketHub WebUI — instrument catalog search + provider sync.
 *
 * Owns the Instruments view: debounced catalog search, result rendering
 * (with add-to-watchlist), provider master sync, and sync-state display.
 */

import { $, escDash } from "./utils.js";
import { loadWatchlists } from "./watchlists.js";

let _instrumentsInitDone = false;

export function initInstruments() {
  if (_instrumentsInitDone) return;
  _instrumentsInitDone = true;
  const input = $("instr-search");
  const msg = $("instr-sync-msg");
  let debounce = null;
  const doSearch = async () => {
    const q = input.value.trim();
    const exchange = $("instr-exchange").value;
    const url = "/api/instruments/search?limit=25" +
      (q ? "&q=" + encodeURIComponent(q) : "") +
      (exchange ? "&exchange=" + exchange : "");
    try {
      const res = await fetch(url);
      const data = await res.json();
      const body = $("instr-body");
      if (!data.results || !data.results.length) {
        body.innerHTML = '<tr><td colspan="9" class="empty-row">No matches. Sync a provider master first if the catalog is empty.</td></tr>';
        return;
      }
      body.innerHTML = data.results.map((r) =>
        `<tr data-tok="${r.instrument_token}" data-ex="${escDash(r.exchange)}" data-sym="${escDash(r.tradingsymbol)}">` +
        `<td>${escDash(r.tradingsymbol)}</td><td>${escDash(r.name) || "—"}</td>` +
        `<td>${escDash(r.exchange)}</td><td>${escDash(r.instrument_type) || "—"}</td>` +
        `<td>${escDash(r.expiry) || "—"}</td><td>${r.strike != null ? r.strike : "—"}</td>` +
        `<td>${r.lot_size != null ? r.lot_size : "—"}</td><td>${escDash(r.provider)}</td>` +
        `<td><button class="btn wl-add btn-xs">+ Watchlist</button></td></tr>`
      ).join("");
    } catch { /* silent */ }
  };
  input.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(doSearch, 300);
  });
  $("instr-exchange").addEventListener("change", doSearch);
  document.getElementById("instr-table").addEventListener("click", async (e) => {
    const btn = e.target.closest(".wl-add");
    if (!btn) return;
    const tr = btn.closest("tr");
    try {
      let wlId = null;
      const res = await fetch("/api/watchlists");
      const data = await res.json();
      if (data.watchlists && data.watchlists.length) {
        wlId = data.watchlists[0].id;
      } else {
        const created = await fetch("/api/watchlists", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: "Default" }) });
        const cd = await created.json();
        wlId = cd.watchlist.id;
      }
      await fetch(`/api/watchlists/${wlId}/items`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ exchange: tr.dataset.ex,
          instrument_token: tr.dataset.tok,
          tradingsymbol: tr.dataset.sym }) });
      msg.textContent = `Added ${tr.dataset.sym} to watchlist.`;
      msg.className = "hint ok";
      loadWatchlists();
    } catch {
      msg.textContent = "Failed to add to watchlist.";
      msg.className = "hint err";
    }
  });
  const doSync = async (provider, btn) => {
    btn.disabled = true;
    msg.textContent = `Syncing ${provider} instrument master…`;
    msg.className = "hint";
    try {
      const res = await fetch("/api/instruments/sync", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider }) });
      const data = await res.json();
      msg.textContent = res.ok
        ? `${provider} sync complete: ${data.records} instruments.`
        : (data.error || "Sync failed.");
      msg.className = "hint " + (res.ok ? "ok" : "err");
      doSearch();
      if (typeof loadSyncState === "function") loadSyncState();
    } catch {
      msg.textContent = "Network error during sync.";
      msg.className = "hint err";
    } finally { btn.disabled = false; }
  };
  $("instr-sync-upstox").addEventListener("click",
    (e) => doSync("upstox", e.target));
  $("instr-sync-fyers").addEventListener("click",
    (e) => doSync("fyers", e.target));

  async function loadSyncState() {
    try {
      const res = await fetch("/api/instruments/sync-state");
      const d = await res.json();
      const parts = (d.providers || []).map((p) =>
        `${p.provider}: ${p.instruments} instruments` +
        (p.last_sync ? ` (synced ${new Date(p.last_sync).toLocaleString()})`
                     : " (never synced)"));
      $("instr-sync-state").textContent = parts.length
        ? "Catalog — " + parts.join(" | ")
        : "Catalog empty — sync a provider master to enable search.";
    } catch { /* silent */ }
  }
  loadSyncState();

  doSearch();
}
