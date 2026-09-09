// Read a CSS custom property value as a concrete color for ECharts (canvas).
//
// Ported from the legacy WebUI: a hidden probe element resolves var(--x) (and
// color-mix) via getComputedStyle. The probe is created lazily on first use so
// importing this module has NO import-time DOM side effects.

let probe: HTMLDivElement | null = null;

function ensureProbe(): HTMLDivElement {
  if (probe) return probe;
  probe = document.createElement("div");
  probe.style.display = "none";
  document.body.appendChild(probe);
  return probe;
}

export function cssVar(name: string, fallback = "#888"): string {
  const el = ensureProbe();
  el.style.color = `var(${name})`;
  const value = getComputedStyle(el).color;
  if (value && value !== "rgba(0, 0, 0, 0)") return value;
  el.style.color = "var(--text-muted)";
  const fb = getComputedStyle(el).color;
  return fb && fb !== "rgba(0, 0, 0, 0)" ? fb : fallback;
}

// Build an ECharts color object that resolves theme tokens at call time.
export function themeColors() {
  return {
    pos: cssVar("--pos"),
    neg: cssVar("--neg"),
    accent: cssVar("--accent"),
    info: cssVar("--info"),
    textMuted: cssVar("--text-muted"),
    textFaint: cssVar("--text-faint"),
    border: cssVar("--border"),
    surface: cssVar("--surface-1"),
    surface2: cssVar("--surface-2"),
    text: cssVar("--text"),
  };
}
