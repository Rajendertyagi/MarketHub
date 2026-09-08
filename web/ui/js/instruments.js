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
        `<td><button class="btn btn-compact wl-add">+ Watchlist</button></td></tr>`
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

  // ── Data Segments panel (catalog segment enable/disable) ──────────────
  // Checkboxes change LOCAL state only; Save & Re-sync persists the
  // preference and explicitly re-syncs providers whose set changed.
  const SEGMENT_INFO = {
    NSE_EQ: "NSE stocks & ETFs (cash market)",
    NSE_FO: "NSE stock & index futures/options",
    NSE_INDEX: "NSE indices (Nifty, Bank Nifty, FinNifty…)",
    NSE_COM: "NSE commodity derivatives (Gold, Silver, Crude…)",
    BSE_EQ: "BSE stocks & ETFs",
    BSE_FO: "BSE stock & index futures/options",
    BSE_INDEX: "BSE indices (Sensex, Bankex)",
    MCX_FO: "MCX commodity futures/options (Gold, Silver, Crude…)",
    BCD_FO: "BSE currency derivatives (USDINR, EURINR…)",
    NCD_FO: "NSE currency derivatives (USDINR, EURINR…)",
    GLOBAL: "Global/other instruments",
  };
  const GROUPS = [
    { label: "NSE", segs: ["NSE_EQ", "NSE_FO", "NSE_INDEX", "NSE_COM"] },
    { label: "BSE", segs: ["BSE_EQ", "BSE_FO", "BSE_INDEX"] },
    { label: "OTHER", segs: ["MCX_FO", "BCD_FO", "NCD_FO", "GLOBAL"] },
  ];
  let _segState = { enabled: [], segments: [] };

  async function loadSegments() {
    const wrap = $("seg-groups");
    if (!wrap) return;
    try {
      const res = await fetch("/api/instruments/segments");
      const data = await res.json();
      _segState = { enabled: new Set(data.enabled || []),
        segments: data.segments || [] };
      const bySeg = new Map((_segState.segments || []).map(
        (s) => [s.segment, s]));
      wrap.innerHTML = "";
      for (const g of GROUPS) {
        const group = document.createElement("div");
        group.className = "seg-group";
        const head = document.createElement("div");
        head.className = "seg-group-label";
        head.textContent = g.label;
        group.appendChild(head);
        for (const seg of g.segs) {
          const info = bySeg.get(seg);
          if (!info) continue;   // provider never discovered it
          const label = document.createElement("label");
          label.className = "seg-check";
          label.title = SEGMENT_INFO[seg] || seg;
          const cb = document.createElement("input");
          cb.type = "checkbox";
          cb.checked = !!info.enabled;
          cb.dataset.seg = seg;
          label.appendChild(cb);
          const text = document.createElement("span");
          text.className = "seg-text";
          const name = document.createElement("b");
          name.textContent = seg;
          text.appendChild(name);
          text.appendChild(document.createElement("br"));
          const desc = document.createElement("small");
          desc.className = "seg-desc";
          desc.textContent = SEGMENT_INFO[seg] || seg;
          text.appendChild(desc);
          label.appendChild(text);
          const count = document.createElement("span");
          count.className = "seg-count";
          count.textContent = (info.catalog_rows || 0).toLocaleString();
          label.appendChild(count);
          group.appendChild(label);
        }
        wrap.appendChild(group);
      }
    } catch { /* silent — panel stays empty */ }
  }

  $("seg-save-resync")?.addEventListener("click", async (e) => {
    const btn = e.target;
    const wrap = $("seg-groups");
    const chosen = [...(wrap || document).querySelectorAll(
      "input[type=checkbox][data-seg]:checked")].map(
      (cb) => cb.dataset.seg);
    if (!chosen.length) {
      $("seg-result").textContent
        = "At least one segment must stay enabled.";
      $("seg-result").className = "hint err";
      return;
    }
    const before = new Set(_segState.enabled || []);
    const after = new Set(chosen);
    const changed = before.size !== after.size
      || [...after].some((s) => !before.has(s));
    btn.disabled = true;
    $("seg-result").textContent = "Saving preference…";
    $("seg-result").className = "hint";
    try {
      const put = await fetch("/api/instruments/segments", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ segments: chosen }) });
      const putData = await put.json();
      if (!put.ok) {
        $("seg-result").textContent = putData.error || "Save failed.";
        $("seg-result").className = "hint err";
        return;
      }
      if (!changed) {
        $("seg-result").textContent
          = `Preference saved (unchanged) — catalog already matches.`;
        return;
      }
      // Re-sync providers whose catalog must change (Upstox always
      // defines the base universe; Fyers rows are derived the same way).
      const parts = [];
      for (const provider of ["upstox", "fyers"]) {
        $("seg-result").textContent = `Re-syncing ${provider}…`;
        const res = await fetch("/api/instruments/sync", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider }) });
        const data = await res.json();
        parts.push(res.ok
          ? `${provider}: kept ${data.kept}/${data.parsed}`
            + ` (filtered ${data.filtered})`
          : `${provider}: sync failed`);
      }
      $("seg-result").textContent = `Saved ${chosen.length} segments — `
        + parts.join(" | ");
      $("seg-result").className = "hint ok";
      loadSyncState();
      loadSegments();
      doSearch();
    } catch {
      $("seg-result").textContent = "Network error during save/re-sync.";
      $("seg-result").className = "hint err";
    } finally { btn.disabled = false; }
  });
  loadSegments();

  doSearch();
}
