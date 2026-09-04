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
  const initial = document.getElementById("view-" + hashView)
    ? hashView
    : (document.getElementById("view-" + saved) ? saved : "dashboard");
  switchView(initial);
}

export function switchView(view) {
  currentView = view;
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  const el = $("view-" + view);
  if (el) el.classList.add("active");
  document.querySelectorAll(".nav-link").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view);
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
