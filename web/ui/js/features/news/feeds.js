/**
 * MarketHub WebUI — News source rail (N-UI1.1).
 *
 * Quick source navigation ONLY. No filter form, no inputs, no debounce —
 * those live in filters.js (single canonical toolbar). Clicking a source
 * is equivalent to changing the Source toolbar filter: both mutate the
 * same store.filters.sourceId. Counts come from the CURRENT LOADED result
 * set (countsBySource); never a per-source backend request.
 */

import { $, esc, escAttr } from "../../utils.js";

export function initFeedsUI(store, hooks) {
  const listEl = $("news-source-list");

  function renderSources() {
    if (!listEl) return;
    const counts = store.countsBySource();
    const total = store.order.length;
    const active = store.filters.sourceId || "";
    let html = `<li><button type="button" class="news-source-item${active === "" ? " active" : ""}"` +
      ` data-news-source=""${active === "" ? ' aria-current="true"' : ""}` +
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
  }

  if (listEl) {
    // Delegated once; the <ul> persists across re-renders.
    listEl.addEventListener("click", (e) => {
      const btn = e.target && e.target.closest ? e.target.closest("[data-news-source]") : null;
      if (!btn || !hooks.onFilters) return;
      // Rail is authoritative for sourceId; remaining filters merge from
      // current store state in index.js (no stale-control fallback).
      hooks.onFilters(
        { sourceId: btn.getAttribute("data-news-source") || "" },
        { sourceChanged: true });
    });
  }

  store.on("articles", renderSources);
  store.on("filters", renderSources);
  return { renderSources };
}
