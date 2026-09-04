/**
 * MarketHub WebUI — News feature entry (N-UI1).
 *
 * Owns init/open lifecycle, API load orchestration, pane wiring, and the
 * refresh lifecycle (selection preservation + new-arrival pill). Rendering
 * belongs to feeds.js / article-list.js / reader.js / sentiment.js; shared
 * mutation belongs to state.js. No News logic may live in app.js.
 */

import { $ } from "../../utils.js";
import { apiPost } from "../../api.js";
import { getNewsSources, loadNewsSources } from "../../sources.js";
import { createNewsStore } from "./state.js";
import { aggregateSentiment, aggregateText } from "./sentiment.js";
import { initFeedsUI } from "./feeds.js";
import { initFiltersUI } from "./filters.js";
import { initArticleListUI } from "./article-list.js";
import { initReaderUI } from "./reader.js";

const PAGE_LIMIT = 50;

const store = createNewsStore();
let _wired = false;
let _list = null;

function buildParams(limit) {
  const f = store.filters;
  const params = new URLSearchParams();
  if (f.sourceId) params.set("source_ids", f.sourceId);
  if (f.category) params.set("categories", f.category);
  if (f.keywords) params.set("keywords_include", f.keywords);
  if (f.symbol) params.set("symbol", f.symbol);
  if (f.maxAgeH && !isNaN(Number(f.maxAgeH)) && Number(f.maxAgeH) > 0) {
    params.set("max_age_hours", String(Number(f.maxAgeH)));
  }
  params.set("limit", String(limit || PAGE_LIMIT));
  return params;
}

function renderStrip() {
  const aggEl = $("news-agg");
  if (aggEl) {
    const agg = aggregateSentiment(store.order, store.sentiment.byId);
    aggEl.textContent = store.order.length || store.loadedOnce
      ? aggregateText(store.order.length, agg)
      : "No articles loaded";
  }
}

/** Load sentiment for the current result set; align positionally by item_id. */
async function loadSentiment() {
  try {
    const resp = await fetch("/api/news/sentiment?" + buildParams(PAGE_LIMIT).toString());
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    const articles = data.articles || [];
    const sentiments = data.sentiments || [];
    // The wire contract keys sentiments by stable store id while articles
    // carry provider ids, so alignment is positional over the same query.
    if (articles.length && articles.length === sentiments.length) {
      const byId = new Map();
      articles.forEach((a, i) => {
        if (a && a.item_id && sentiments[i]) byId.set(a.item_id, sentiments[i]);
      });
      store.setSentiment(byId, aggregateSentiment(store.order, byId));
    } else {
      store.setSentiment(new Map(), null);
    }
  } catch {
    store.setSentiment(new Map(), null);
  }
  renderStrip();
}

async function loadArticles({ refresh = false, resetScroll = false, resetSelection = false } = {}) {
  const first = !store.loadedOnce;
  store.setStatus({ loading: first && !refresh, refreshing: !first && refresh, error: null });
  if (first && !refresh) renderStrip();
  try {
    const resp = await fetch("/api/news?" + buildParams(PAGE_LIMIT).toString());
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    const prevOrder = store.order.slice();
    const fresh = store.setArticles(data.articles || [], {
      resetScroll,
      resetSelection,
      prevOrder: refresh ? prevOrder : null,
    });
    store.setStatus({ loading: false, refreshing: false, error: null });
    renderStrip();
    // New-arrival pill (§12): selection + content kept; user opts into the jump.
    const pill = $("news-new-pill");
    if (pill) {
      if (refresh && fresh && fresh.length) {
        pill.textContent = `${fresh.length} new article${fresh.length === 1 ? "" : "s"} — Show`;
        pill.classList.remove("hidden");
      } else {
        pill.classList.add("hidden");
      }
    }
    loadSentiment();
  } catch (e) {
    store.setStatus({ loading: false, refreshing: false, error: e && e.message });
    renderStrip();
  }
}

async function refreshNews() {
  const b1 = $("news-refresh");
  if (b1) b1.disabled = true;
  const errEl = $("news-error");
  try {
    await apiPost("/api/news/refresh", {});
    await loadArticles({ refresh: true });
  } catch (e) {
    // Refresh failure must not destroy already-rendered content.
    if (errEl) {
      errEl.textContent = "Refresh failed: " + ((e && e.message) || "unknown error");
      errEl.classList.remove("hidden");
      setTimeout(() => errEl.classList.add("hidden"), 6000);
    }
  }
  if (b1) b1.disabled = false;
}

/**
 * View-enter hook (registered once via the router): sources first, then
 * instant history render. In-memory state survives view switches — no
 * refetch merely because the user navigated away and back.
 */
export async function openNews() {
  await loadNewsSources();
  store.sources = getNewsSources().slice();
  if (!store.loadedOnce) {
    syncFilterInputs();
    await loadArticles();
  } else {
    renderStrip();
  }
}

function syncFilterInputs() {
  const f = store.filters;
  const set = (id, v) => { const el = $(id); if (el && document.activeElement !== el) el.value = v; };
  set("news-filter-source", f.sourceId);
  set("news-filter-category", f.category);
  set("news-filter-keywords", f.keywords);
  set("news-filter-symbol", f.symbol);
  set("news-filter-max-age", f.maxAgeH);
}

/** Canonical filter commit: merge partial updates, resolve scope, reload. */
function commitFilters(patch, meta) {
  const sourceChanged = !!(meta && meta.sourceChanged);
  const prevSource = store.filters.sourceId || "";
  const next = { ...store.filters };
  Object.keys(patch || {}).forEach((k) => {
    if (k in next) next[k] = patch[k] || "";
  });
  const scopeChanged = sourceChanged || (next.sourceId || "") !== prevSource;
  const sameScope = !scopeChanged;
  store.setFilters(next);
  syncFilterInputs();
  // Scope change → first article + scroll reset; same-scope edits
  // preserve selection when still present (§11).
  loadArticles({ resetScroll: !sameScope, resetSelection: scopeChanged });
}

/** Chip × / Clear all: empty one filter ("" = all filters). */
function clearFilter(key) {
  const patch = {};
  if (!key) {
    ["sourceId", "category", "symbol", "maxAgeH", "keywords"].forEach((k) => { patch[k] = ""; });
  } else {
    patch[key] = "";
  }
  commitFilters(patch, { sourceChanged: key === "" || key === "sourceId" });
}

export function initNewsUI() {
  if (_wired) return;
  _wired = true;
  initFeedsUI(store, { onFilters: (f, meta) => commitFilters(f, meta) });
  initFiltersUI(store, {
    onFilters: (f, meta) => commitFilters(f, meta),
    onClearFilter: (key) => clearFilter(key),
  });
  _list = initArticleListUI(store);
  initReaderUI(store, { onStep: (d) => { if (_list) _list.selectIndex(d); } });

  const r1 = $("news-refresh");
  if (r1) r1.addEventListener("click", () => refreshNews());
  const pill = $("news-new-pill");
  if (pill) {
    pill.addEventListener("click", () => {
      pill.classList.add("hidden");
      const listEl = $("news-articles-list");
      if (listEl) listEl.scrollTop = 0;
    });
  }
  store.on("articles", renderStrip);
}

/** Test seam: reset module state between isolated checks. */
export function __resetNewsForTests() {
  _wired = false;
}
