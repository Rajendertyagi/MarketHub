/**
 * MarketHub WebUI — News feed rail (N-UI1).
 *
 * Owns: source list + per-source counts (computed from the CURRENT LOADED
 * result set — never a per-source backend request), compact filter inputs,
 * and the compact tablet/phone source selector. Emits intent via callbacks;
 * renders only from store state. Source CRUD stays in sources.js/Settings.
 */

import { $, esc, escAttr } from "../../utils.js";

const DEBOUNCE_MS = 400;

export function initFeedsUI(store, hooks) {
  const listEl = $("news-source-list");
  const compactSel = $("news-filter-source");
  let debounceTimer = 0;

  function sourceName(sid) {
    const s = (store.sources || []).find((x) => x.source_id === sid);
    return (s && s.name) || sid;
  }

  function renderSources() {
    if (!listEl) return;
    const counts = store.countsBySource();
    const total = store.order.length;
    const active = store.filters.sourceId || "";
    let html = `<li><button type="button" class="news-source-item${active === "" ? " active" : ""}"` +
      ` data-news-source="" aria-current="${active === "" ? "true" : "false"}"` +
      ` title="All loaded articles"><span>All Sources</span>` +
      `<span class="count">${total}</span></button></li>`;
    (store.sources || []).forEach((s) => {
      const sid = s.source_id;
      const c = counts.get(sid) || 0;
      const isActive = active === sid;
      html += `<li><button type="button" class="news-source-item${isActive ? " active" : ""}"` +
        ` data-news-source="${escAttr(sid)}"${isActive ? ' aria-current="true"' : ""}` +
        ` title="${escAttr(s.name || sid)} — ${c} in loaded results">` +
        `<span>${esc(s.name || sid)}</span><span class="count">${c}</span></button></li>`;
    });
    listEl.innerHTML = html;
    if (compactSel) {
      compactSel.innerHTML = '<option value="">All Sources</option>';
      (store.sources || []).forEach((s) => {
        const opt = document.createElement("option");
        opt.value = s.source_id;
        opt.textContent = `${s.name} (${counts.get(s.source_id) || 0})`;
        compactSel.appendChild(opt);
      });
      // Mirror rail selection; unknown ids fall back to "All Sources".
      compactSel.value = store.filters.sourceId || "";
    }
  }

  function readInputs() {
    // NOTE: "" means "All Sources" — never fall back to stale store state.
    const sid = compactSel ? compactSel.value : (store.filters.sourceId || "");
    return {
      sourceId: sid || "",
      category: $("news-filter-category")?.value.trim() || "",
      keywords: $("news-filter-keywords")?.value.trim()
        || $("news-filter-keywords-m")?.value.trim() || "",
      symbol: $("news-filter-symbol")?.value.trim() || "",
      maxAgeH: $("news-filter-max-age")?.value.trim() || "",
    };
  }

  function commitFilters(sourceChanged, overrideSourceId) {
    const f = readInputs();
    if (overrideSourceId !== undefined) {
      // Rail-initiated change: the rail button is authoritative, not the
      // (possibly stale) compact selector.
      f.sourceId = overrideSourceId;
      if (compactSel) compactSel.value = overrideSourceId;
    }
    hooks.onFilters(f, { sourceChanged: !!sourceChanged });
  }

  function scheduleCommit() {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => { debounceTimer = 0; commitFilters(false); }, DEBOUNCE_MS);
  }

  if (listEl) {
    listEl.addEventListener("click", (e) => {
      const btn = e.target && e.target.closest ? e.target.closest("[data-news-source]") : null;
      if (!btn) return;
      const sid = btn.getAttribute("data-news-source") || "";
      commitFilters(true, sid);
    });
  }
  if (compactSel) {
    compactSel.addEventListener("change", () => commitFilters(true));
  }
  ["news-filter-category", "news-filter-keywords", "news-filter-symbol", "news-filter-max-age"].forEach((id) => {
    const el = $(id);
    if (!el) return;
    el.addEventListener("input", scheduleCommit);
    el.addEventListener("change", () => {
      if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = 0; }
      commitFilters(false);
    });
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = 0; }
        commitFilters(false);
      }
    });
  });
  const compactKw = $("news-filter-keywords-m");
  if (compactKw) {
    compactKw.addEventListener("input", scheduleCommit);
    compactKw.addEventListener("change", () => {
      if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = 0; }
      commitFilters(false);
    });
    compactKw.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = 0; }
        commitFilters(false);
      }
    });
  }

  store.on("articles", renderSources);
  store.on("filters", renderSources);
  return { renderSources, sourceName };
}
