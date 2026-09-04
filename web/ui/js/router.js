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

import { $ } from "./utils.js";

const _enterHooks = new Map();   // view -> Set<fn>
let _routerBound = false;

// ── Active view state ─────────────────────────────────────────────────────
// Single owner of "which view is shown". Exported as a live binding so
// feature modules (e.g. market sources) can read it without globals.
export let currentView = "dashboard";

export function initNav() {
  document.querySelectorAll(".nav-link").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.view));
  });
  const hashView = location.hash.startsWith("#/")
    ? location.hash.slice(2) : null;
  let saved = null;
  try { saved = sessionStorage.getItem("mh-last-view"); } catch {}
  // Deep links ("settings/brokers") resolve against their base view.
  const baseOf = (v) => v && v.indexOf("/") > 0 ? v.slice(0, v.indexOf("/")) : v;
  const initial = document.getElementById("view-" + hashView)
    ? hashView
    : (document.getElementById("view-" + baseOf(hashView)) ? hashView
    : (document.getElementById("view-" + saved) ? saved : "dashboard"));
  switchView(initial);
}

export function switchView(view) {
  currentView = view;
  // Sub-routes ("settings/brokers") activate their base view section.
  const base = view.indexOf("/") > 0 ? view.slice(0, view.indexOf("/")) : view;
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  const el = $("view-" + base);
  if (el) el.classList.add("active");
  document.querySelectorAll(".nav-link").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view || b.dataset.view === base);
  });
  // NOTE: the old `if (view === "sources") pollSources()` branch is gone —
  // no `sources` view or nav entry exists, so it was dead code. Sources
  // status keeps its boot poll + 10s interval from app initialization.
  try {
    sessionStorage.setItem("mh-last-view", view);
    if (location.hash !== "#/" + view) {
      history.replaceState(null, "", location.pathname + "#/" + view);
    }
  } catch { /* storage unavailable */ }
}

function _currentView() {
  const h = location.hash || "";
  return h.startsWith("#/") ? h.slice(2) : null;
}

function _fire(view) {
  if (!view) return;
  // Fire exact-view hooks, then base-segment hooks so a generic handler
  // (e.g. "settings") also runs for deep links ("settings/brokers").
  // Each set fires once; duplicate registrations collapse via Set.
  const seen = new Set();
  const slash = view.indexOf("/");
  const names = slash > 0 ? [view, view.slice(0, slash)] : [view];
  names.forEach((name) => {
    if (seen.has(name)) return;
    seen.add(name);
    const hooks = _enterHooks.get(name);
    if (!hooks) return;
    hooks.forEach((fn) => {
      try {
        const r = fn(view);
        if (r && typeof r.catch === "function") r.catch(() => {});
      } catch { /* a view hook must never break routing */ }
    });
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
  // Back/forward buttons: activate the base view when it changed (nav
  // clicks go through switchView directly; hash-only changes otherwise
  // leave a stale view active), then fire enter hooks.
  window.addEventListener("hashchange", () => {
    const v = _currentView();
    if (v) {
      const base = v.indexOf("/") > 0 ? v.slice(0, v.indexOf("/")) : v;
      const active = document.querySelector(".view.active");
      if (document.getElementById("view-" + base) &&
          (!active || active.id !== "view-" + base)) {
        switchView(v);
      }
    }
    _fire(v);
  });
  // Direct load / F5 with a #/view hash.
  _fire(_currentView());
}
