/**
 * MarketHub WebUI — News filter toolbar + chips (N-UI1.1).
 *
 * Single canonical filter controls for ALL viewport modes (no separate
 * desktop/tablet/phone implementations). Renders from FILTER_DEFS so
 * future backend-supported dimensions plug in without redesigning News;
 * chips mutate the same canonical store.filters state as every control.
 *
 * Real backend params today: source_ids, categories, symbol, max_age_hours,
 * keywords_include. Nothing here invents taxonomy (no Index/Sector/etc).
 */

import { $, esc, escAttr } from "../../utils.js";

const DEBOUNCE_MS = 400;

/**
 * Filter definitions: key = store.filters field, label = chip label,
 * input = toolbar element id, kind = text|select|source.
 * To add a future dimension: append a def + backend param mapping in
 * index.js buildParams. No other News file changes.
 */
export const FILTER_DEFS = [
  { key: "sourceId", label: "Source", input: "news-filter-source", kind: "source" },
  { key: "category", label: "Category", input: "news-filter-category", kind: "text" },
  { key: "symbol", label: "Symbol", input: "news-filter-symbol", kind: "text" },
  { key: "maxAgeH", label: "Age", input: "news-filter-max-age", kind: "select" },
  { key: "keywords", label: "Search", input: "news-filter-keywords", kind: "text" },
];

function chipValue(def, store) {
  if (def.key === "sourceId") {
    const sid = store.filters.sourceId || "";
    if (!sid) return "";
    const s = (store.sources || []).find((x) => x.source_id === sid);
    return (s && s.name) || sid;
  }
  return (store.filters[def.key] || "").trim();
}

export function initFiltersUI(store, hooks) {
  const chipsEl = $("news-chips");
  const sourceSel = $("news-filter-source");
  let debounceTimer = 0;

  function readToolbar() {
    const f = {};
    FILTER_DEFS.forEach((def) => {
      const el = $(def.input);
      f[def.key] = el ? (el.value || "").trim() : "";
    });
    return f;
  }

  function commit(sourceChanged) {
    hooks.onFilters(readToolbar(), { sourceChanged: !!sourceChanged });
  }

  function scheduleCommit() {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => { debounceTimer = 0; commit(false); }, DEBOUNCE_MS);
  }

  function flushCommit() {
    if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = 0; }
    commit(false);
  }

  // Populate the Source <select> from the registry (names only; the rail
  // is the counts surface). Mirrors rail selection via store state.
  function renderSourceOptions() {
    if (!sourceSel) return;
    sourceSel.innerHTML = '<option value="">All Sources</option>';
    (store.sources || []).forEach((s) => {
      const opt = document.createElement("option");
      opt.value = s.source_id;
      opt.textContent = s.name || s.source_id;
      sourceSel.appendChild(opt);
    });
    sourceSel.value = store.filters.sourceId || "";
  }

  function renderChips() {
    if (!chipsEl) return;
    const active = FILTER_DEFS
      .map((def) => ({ def, value: chipValue(def, store) }))
      .filter((c) => c.value);
    let html = "";
    active.forEach(({ def, value }) => {
      // MarketHub chip: label + native remove button (both carry data-news-chip
      // so a click on either resolves to the same filter key).
      html += `<span class="ui-chip" data-news-chip="${escAttr(def.key)}"` +
        ` title="Remove ${escAttr(def.label)} filter">` +
        `${esc(def.label)}: ${esc(value)}` +
        `<button type="button" class="ui-chip-remove" aria-label="Remove ${escAttr(def.label)} filter">×</button>` +
        `</span>`;
    });
    if (active.length > 1) {
      html += `<button type="button" class="ui-btn ui-btn-sm" data-news-clear>Clear all</button>`;
    }
    chipsEl.innerHTML = html;
  }

  if (chipsEl) {
    // Chip × button or Clear-all both resolve to onClearFilter.
    chipsEl.addEventListener("click", (e) => {
      const chip = e.target && e.target.closest ? e.target.closest("[data-news-chip]") : null;
      if (chip && hooks.onClearFilter) {
        hooks.onClearFilter(chip.getAttribute("data-news-chip") || "");
        return;
      }
      const btn = e.target && e.target.closest ? e.target.closest("[data-news-clear]") : null;
      if (btn && hooks.onClearFilter) hooks.onClearFilter("");
    });
  }

  FILTER_DEFS.forEach((def) => {
    const el = $(def.input);
    if (!el) return;
    if (def.kind === "text") {
      el.addEventListener("input", scheduleCommit);
      el.addEventListener("change", flushCommit);
      el.addEventListener("keydown", (ev) => { if (ev.key === "Enter") flushCommit(); });
    } else {
      // select + source: commit immediately, no debounce.
      el.addEventListener("change", () => commit(def.kind === "source"));
    }
  });

  store.on("articles", () => { renderSourceOptions(); renderChips(); });
  store.on("filters", () => { renderSourceOptions(); renderChips(); });

  return { renderSourceOptions, renderChips, readToolbar };
}
