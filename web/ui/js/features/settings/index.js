/**
 * MarketHub WebUI — Settings workspace shell.
 *
 * Owns the Settings sidebar navigation and section panels:
 *   - sidebar clicks swap panels without a page reload
 *   - deep links (#/settings/<section>) work on direct load, F5,
 *     back/forward, and from in-app links (e.g. News "Manage Sources")
 *   - listeners are registered exactly once; panel switches never
 *     duplicate timers, streams, or bindings
 *
 * Section content stays owned by its feature module:
 *   general      → ./general.js      (app preferences)
 *   brokers      → ../auth.js        (Upstox/Fyers forms, bound by ID)
 *   news-sources → ../sources.js     (CRUD table + modal, bound by ID)
 *   market-sources → ../market-sources.js (poll-driven detail tables)
 *   alerts/ai-mcp/logging → static links to operational views
 *   data-retention → informational placeholder (no knobs yet)
 *   backup       → ./backup.js       (backup button)
 */

import { switchView } from "../../router.js";
import { loadNewsSources } from "../../sources.js";

const SECTIONS = ["general", "brokers", "news-sources", "market-sources",
  "alerts", "ai-mcp", "data-retention", "logging", "backup"];

let _settingsBound = false;

function _sectionFromHash() {
  const h = location.hash || "";
  const m = /^#\/settings\/([A-Za-z0-9_-]+)\/?$/.exec(h);
  const sec = m ? m[1] : null;
  return SECTIONS.includes(sec) ? sec : "general";
}

function _settingsViewActive() {
  const el = document.getElementById("view-settings");
  return !!(el && el.classList.contains("active"));
}

export function showSettingsSection(section) {
  const sec = SECTIONS.includes(section) ? section : "general";
  if (!_settingsViewActive()) {
    // Activate the Settings view without clobbering a deep-link hash.
    switchView("settings/" + sec);
  }
  document.querySelectorAll(".set-panel").forEach((p) => {
    p.classList.toggle("hidden", p.id !== "set-panel-" + sec);
  });
  document.querySelectorAll(".set-nav-link").forEach((b) => {
    b.classList.toggle("active", b.dataset.section === sec);
  });
  const want = "#/settings/" + sec;
  if (location.hash !== want) {
    history.replaceState(null, "", location.pathname + want);
  }
  // The News Sources table is populated on demand (same idempotent loader
  // the News view uses) so the panel never sticks on "Loading…".
  if (sec === "news-sources") {
    try {
      const r = loadNewsSources();
      if (r && typeof r.catch === "function") r.catch(() => {});
    } catch { /* loader is failure-silent */ }
  }
}

export function initSettingsUI() {
  if (_settingsBound) return;
  _settingsBound = true;
  document.querySelectorAll(".set-nav-link").forEach((btn) => {
    btn.addEventListener("click", () => showSettingsSection(btn.dataset.section));
  });
  // Deep links, F5, back/forward: the router fires the "settings" hook
  // for #/settings/* hashes; render the requested section then.
  window.addEventListener("hashchange", () => {
    const h = location.hash || "";
    if (h === "#/settings" || h.startsWith("#/settings/")) {
      showSettingsSection(_sectionFromHash());
    }
  });
  // Direct load with a settings hash.
  const h = location.hash || "";
  if (h === "#/settings" || h.startsWith("#/settings/")) {
    showSettingsSection(_sectionFromHash());
  }
}
