// Read a CSS custom property value as a concrete color for ECharts.
//
// Ported from the legacy WebUI: a hidden probe element resolves var(--x) (and
// color-mix) via getComputedStyle. The probe is created lazily on first use so
// importing this module has NO import-time DOM side effects.
//
// ECharts/zrender cannot parse modern color spaces such as oklch()/oklab(), which
// this design system uses for its tokens. We therefore convert every resolved
// color from OKLab/OKLCH to plain rgb()/rgba() so ECharts can consume it.

let probe: HTMLDivElement | null = null;

function ensureProbe(): HTMLDivElement {
  if (probe) return probe;
  probe = document.createElement("div");
  probe.style.display = "none";
  document.body.appendChild(probe);
  return probe;
}

// Convert OKLab/OKLCH (used by this design system's tokens) into an rgb()/rgba()
// string ECharts/zrender can parse. ECharts cannot parse oklch()/oklab() color
// spaces, so every theme token resolved through this module is normalized to
// plain sRGB. Other color forms (rgb/hsl/hex) are returned unchanged.
function oklabToRgb(L: number, a: number, b: number, alpha: number): string {
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ * l_ * l_;
  const m = m_ * m_ * m_;
  const s = s_ * s_ * s_;
  let r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
  let g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
  let bl = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s;
  const enc = (x: number) => {
    const v = x <= 0.0031308 ? 12.92 * x : 1.055 * Math.pow(x, 1 / 2.4) - 0.055;
    return Math.round(Math.max(0, Math.min(1, v)) * 255);
  };
  const R = enc(r);
  const G = enc(g);
  const B = enc(bl);
  return alpha < 1 ? `rgba(${R}, ${G}, ${B}, ${alpha})` : `rgb(${R}, ${G}, ${B})`;
}

function parseColor(value: string): string {
  if (!value) return value;
  const num = (s: string) => parseFloat(s);
  const grp = (m: RegExpMatchArray, i: number): string => m[i] ?? "";
  const oklch = value.match(
    /^oklch\(\s*([\d.]+)%?\s+([\d.\-]+)\s+([\d.\-]+)(?:\s*\/\s*([\d.]+)%?\s*)?\)$/,
  );
  if (oklch) {
    const L = num(grp(oklch, 1)) / (grp(oklch, 1).includes("%") ? 100 : 1);
    const C = num(grp(oklch, 2));
    const H = (num(grp(oklch, 3)) * Math.PI) / 180;
    const alpha = grp(oklch, 4)
      ? num(grp(oklch, 4)) / (grp(oklch, 4).includes("%") ? 100 : 1)
      : 1;
    return oklabToRgb(L, C * Math.cos(H), C * Math.sin(H), alpha);
  }
  const oklab = value.match(
    /^oklab\(\s*([\d.\-]+)%?\s+([\d.\-]+)\s+([\d.\-]+)(?:\s*\/\s*([\d.]+)%?\s*)?\)$/,
  );
  if (oklab) {
    const L = num(grp(oklab, 1)) / (grp(oklab, 1).includes("%") ? 100 : 1);
    const a = num(grp(oklab, 2));
    const b = num(grp(oklab, 3));
    const alpha = grp(oklab, 4)
      ? num(grp(oklab, 4)) / (grp(oklab, 4).includes("%") ? 100 : 1)
      : 1;
    return oklabToRgb(L, a, b, alpha);
  }
  return value;
}

export function cssVar(name: string, fallback = "#888"): string {
  const el = ensureProbe();
  el.style.color = `var(${name})`;
  const value = getComputedStyle(el).color;
  if (value && value !== "rgba(0, 0, 0, 0)") return parseColor(value);
  el.style.color = "var(--text-muted)";
  const fb = getComputedStyle(el).color;
  return parseColor(fb && fb !== "rgba(0, 0, 0, 0)" ? fb : fallback);
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
