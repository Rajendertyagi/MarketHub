/**
 * MarketHub WebUI — hash router with per-view enter hooks.
 *
 * Views register an `onEnter` callback once via `onViewEnter(view, fn)`.
 * The router fires it for:
 *   - nav-link clicks (switchView uses history.replaceState, which does
 *     NOT emit hashchange, so clicks are observed directly),
 *   - back/forward navigation (hashchange),
 *   - initial page load / F5 with a `#/view` hash (boot).
 *
 * Enter callbacks are idempotent by contract (fetch + render, guarded
 * SSE/EventSource setup), so near-simultaneous click+hashchange delivery
 * cannot create duplicate listeners, sources, or streams.
 */

const _enterHooks = new Map();   // view -> Set<fn>
let _routerBound = false;

function _currentView() {
  const h = location.hash || "";
  return h.startsWith("#/") ? h.slice(2) : null;
}

function _fire(view) {
  if (!view) return;
  const hooks = _enterHooks.get(view);
  if (!hooks) return;
  hooks.forEach((fn) => {
    try {
      const r = fn(view);
      if (r && typeof r.catch === "function") r.catch(() => {});
    } catch { /* a view hook must never break routing */ }
  });
}

export function onViewEnter(view, fn) {
  if (!view || typeof fn !== "function") return;
  let set = _enterHooks.get(view);
  if (!set) { set = new Set(); _enterHooks.set(view, set); }
  set.add(fn);
}

export function initRouter() {
  if (_routerBound) return;
  _routerBound = true;
  // Nav clicks: switchView() updates the hash via replaceState (silent),
  // so observe clicks directly.
  document.querySelectorAll(".nav-link[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => _fire(btn.dataset.view));
  });
  // Back/forward buttons.
  window.addEventListener("hashchange", () => _fire(_currentView()));
  // Direct load / F5 with a #/view hash.
  _fire(_currentView());
}
