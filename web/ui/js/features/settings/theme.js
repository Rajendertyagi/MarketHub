// features/settings/theme.js
// Multi-theme engine. Themes are sourced from openchamber, each shipped as a
// dark + light variant. The active theme is the single source of truth via the
// [data-theme] attribute on <html>; the selection persists in localStorage and
// is chosen from the Settings → General theme picker.

export const THEMES = [
  { id: "ayu-dark",          label: "Ayu · Dark",          family: "ayu",          mode: "dark" },
  { id: "ayu-light",         label: "Ayu · Light",         family: "ayu",          mode: "light" },
  { id: "carbonfox-dark",    label: "Carbonfox · Dark",    family: "carbonfox",    mode: "dark" },
  { id: "carbonfox-light",   label: "Carbonfox · Light",   family: "carbonfox",    mode: "light" },
  { id: "catppuccin-dark",   label: "Catppuccin · Dark",   family: "catppuccin",   mode: "dark" },
  { id: "catppuccin-light",  label: "Catppuccin · Light",  family: "catppuccin",   mode: "light" },
  { id: "gruvbox-dark",      label: "Gruvbox · Dark",      family: "gruvbox",      mode: "dark" },
  { id: "gruvbox-light",     label: "Gruvbox · Light",     family: "gruvbox",      mode: "light" },
  { id: "mono-dark",         label: "Mono · Dark",         family: "mono",         mode: "dark" },
  { id: "mono-light",        label: "Mono · Light",        family: "mono",         mode: "light" },
  { id: "openchamber-dark",  label: "OpenChamber · Dark",  family: "openchamber",  mode: "dark" },
  { id: "openchamber-light", label: "OpenChamber · Light", family: "openchamber",  mode: "light" },
];

const DEFAULT_THEME = "openchamber-dark";
const STORAGE_KEY = "mh-theme";

function currentThemeId() {
  return document.documentElement.getAttribute("data-theme") || DEFAULT_THEME;
}

export function applyTheme(id) {
  if (!THEMES.some((t) => t.id === id)) id = DEFAULT_THEME;
  document.documentElement.setAttribute("data-theme", id);
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    /* storage may be unavailable; theme still applies for the session */
  }
  const sel = document.getElementById("settings-theme-select");
  if (sel) sel.value = id;
  // Let canvas consumers (ECharts) recolor without a refetch.
  window.dispatchEvent(new CustomEvent("mh-themechange"));
}

export function initTheme() {
  let saved = null;
  try {
    saved = localStorage.getItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
  applyTheme(saved || DEFAULT_THEME);

  // Topbar ◐ opens the Settings theme picker — the single switcher.
  const top = document.getElementById("theme-toggle");
  if (top) {
    top.addEventListener("click", () => {
      location.hash = "#/settings";
    });
  }

  initThemeSettings();
}

function initThemeSettings() {
  const sel = document.getElementById("settings-theme-select");
  if (!sel) return;

  const families = {};
  for (const t of THEMES) (families[t.family] ||= []).push(t);

  sel.innerHTML = "";
  for (const [family, list] of Object.entries(families)) {
    const og = document.createElement("optgroup");
    og.label = family.charAt(0).toUpperCase() + family.slice(1);
    for (const t of list) {
      const opt = document.createElement("option");
      opt.value = t.id;
      opt.textContent = t.mode === "dark" ? "Dark" : "Light";
      og.appendChild(opt);
    }
    sel.appendChild(og);
  }

  sel.value = currentThemeId();
  sel.addEventListener("change", () => applyTheme(sel.value));
}
