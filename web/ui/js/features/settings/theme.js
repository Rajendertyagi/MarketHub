// features/settings/theme.js
// Theme engine. Two supported themes only: dark (primary) and light (optional).
// The active theme is the single source of truth via the [data-theme] attribute
// on <html>; the selection persists in localStorage and is chosen from the
// Settings → General theme picker. Unsupported stored values map to DEFAULT.

export const THEMES = [
  { id: "dark",  label: "Dark",  mode: "dark" },
  { id: "light", label: "Light", mode: "light" },
];

const DEFAULT_THEME = "dark";
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

  sel.innerHTML = "";
  for (const t of THEMES) {
    const opt = document.createElement("option");
    opt.value = t.id;
    opt.textContent = t.label;
    sel.appendChild(opt);
  }

  sel.value = currentThemeId();
  sel.addEventListener("change", () => applyTheme(sel.value));
}
