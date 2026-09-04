/**
 * MarketHub WebUI — News feature shared state (N-UI1).
 *
 * Sole owner of mutable News reader state. Panes (feeds / article-list /
 * reader) subscribe to change events and render themselves; they must
 * never mutate each other's DOM or reach into this module's internals
 * except through the exported store API.
 *
 * Shape:
 *   articles       Map<item_id, article>   (keyed by stable wire item_id)
 *   order          string[]                (display order, newest first)
 *   sources        Array                   (from sources registry)
 *   selectedItemId string | null           (selection source of truth)
 *   filters        {sourceId, category, keywords, symbol, maxAgeH}
 *   loading        bool                    (initial load, no content yet)
 *   refreshing     bool                    (background refresh, content kept)
 *   error          string | null
 *   sentiment      {byId: Map, aggregate, available: bool}
 */

export function createNewsStore() {
  const listeners = new Map(); // event -> Set<fn>

  const store = {
    articles: new Map(),
    order: [],
    sources: [],
    selectedItemId: null,
    filters: { sourceId: "", category: "", keywords: "", symbol: "", maxAgeH: "" },
    loading: false,
    refreshing: false,
    error: null,
    sentiment: { byId: new Map(), aggregate: null, available: false },
    loadedOnce: false,

    on(event, fn) {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event).add(fn);
    },

    emit(event) {
      const set = listeners.get(event);
      if (!set) return;
      set.forEach((fn) => {
        try { fn(store); } catch { /* a pane must never break the store */ }
      });
    },

    /** Replace the article set; resolves selection per the N-UI1 contract. */
    setArticles(articles, opts = {}) {
      const prevSelected = store.selectedItemId;
      const hadArticles = store.order.length > 0;
      store.articles = new Map();
      store.order = [];
      (articles || []).forEach((a) => {
        const id = a && a.item_id;
        if (!id || store.articles.has(id)) return;
        store.articles.set(id, a);
        store.order.push(id);
      });
      // Selection resolution (§11): source change forces first; otherwise
      // preserve when still present; else first; else clear.
      let next = null;
      if (store.order.length) {
        if (!opts.resetSelection && prevSelected && store.articles.has(prevSelected)) {
          next = prevSelected;
        } else {
          next = store.order[0];
        }
      }
      const selectionChanged = next !== prevSelected;
      store.selectedItemId = next;
      store.loadedOnce = true;
      // Scope changes (filter/source) reset list scroll; refresh preserves it.
      store.lastScopeReset = !!opts.resetScroll;
      // New-arrival detection for the refresh pill (§12).
      let fresh = [];
      if (opts.prevOrder && opts.prevOrder.length) {
        const prev = new Set(opts.prevOrder);
        fresh = store.order.filter((id) => !prev.has(id));
      }
      store.emit("articles");
      if (selectionChanged || !hadArticles) store.emit("selection");
      return fresh;
    },

    select(itemId, opts = {}) {
      if (!itemId || !store.articles.has(itemId)) return false;
      if (store.selectedItemId === itemId && !opts.force) return false;
      store.selectedItemId = itemId;
      store.emit("selection");
      return true;
    },

    setFilters(patch) {
      Object.assign(store.filters, patch || {});
      store.emit("filters");
    },

    setStatus(patch) {
      if ("loading" in patch) store.loading = !!patch.loading;
      if ("refreshing" in patch) store.refreshing = !!patch.refreshing;
      if ("error" in patch) store.error = patch.error || null;
      store.emit("status");
    },

    setSentiment(byId, aggregate) {
      store.sentiment = {
        byId: byId || new Map(),
        aggregate: aggregate || null,
        available: !!(byId && byId.size),
      };
      store.emit("sentiment");
    },

    selected() {
      return (store.selectedItemId && store.articles.get(store.selectedItemId)) || null;
    },

    countsBySource() {
      const counts = new Map();
      store.order.forEach((id) => {
        const a = store.articles.get(id);
        const sid = (a && (a.source_id || a.source_name)) || "";
        counts.set(sid, (counts.get(sid) || 0) + 1);
      });
      return counts;
    },
  };
  return store;
}
