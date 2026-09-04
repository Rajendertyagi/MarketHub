/**
 * MarketHub UI — generic, feature-agnostic splitter.
 *
 * Wires every `[data-split]` element: each direct-child `.split-gutter`
 * resizes the START pane by writing `--split-start-size` (a percentage, so
 * it stays fluid across viewport changes). Knows nothing about any specific
 * feature domain.
 *
 * Constraints (per spec):
 *   - pointer + keyboard only; no timers / viewport polling
 *   - no permanent global drag listener (listeners live on the gutter and
 *     are removed on pointerup/cancel via pointer capture)
 *   - no persistence (no storage of pane sizes)
 *   - nested splits are independent (scoped by :scope > .split-gutter)
 */

const KEYBOARD_STEP_PCT = 2;

function isVertical(splitEl) {
  return (
    splitEl.classList.contains("is-vertical") ||
    splitEl.getAttribute("data-split-orientation") === "vertical"
  );
}

function parsePx(value) {
  const v = (value || "").trim();
  if (!v || v.endsWith("%")) return 0;
  const n = parseFloat(v);
  return isNaN(n) ? 0 : n;
}

function containerSize(splitEl, vertical) {
  return vertical ? splitEl.clientHeight : splitEl.clientWidth;
}

function clampBoundsPct(splitEl, vertical) {
  const cs = getComputedStyle(splitEl);
  const size = containerSize(splitEl, vertical);
  const minPx = parsePx(cs.getPropertyValue("--split-min"));
  const maxPx = parsePx(cs.getPropertyValue("--split-max"));
  return {
    min: size > 0 ? (minPx / size) * 100 : 0,
    max: size > 0 ? (maxPx > 0 ? (maxPx / size) * 100 : 100) : 100,
  };
}

function currentStartPct(splitEl, vertical) {
  const pane = splitEl.querySelector(':scope > .pane[data-pane="start"]');
  const size = containerSize(splitEl, vertical);
  if (!pane || size <= 0) return 50;
  const paneSize = vertical ? pane.offsetHeight : pane.offsetWidth;
  return (paneSize / size) * 100;
}

function setStartPct(splitEl, pct) {
  splitEl.style.setProperty("--split-start-size", pct + "%");
}

function onPointerDown(e, splitEl, gutter) {
  e.preventDefault();
  const vertical = isVertical(splitEl);
  let captured = false;
  try {
    gutter.setPointerCapture(e.pointerId);
    captured = true;
  } catch { /* capture optional */ }

  const rect = splitEl.getBoundingClientRect();
  const size = containerSize(splitEl, vertical);
  const { min, max } = clampBoundsPct(splitEl, vertical);

  const move = (ev) => {
    const pos = vertical ? ev.clientY - rect.top : ev.clientX - rect.left;
    let pct = size > 0 ? (pos / size) * 100 : 0;
    pct = Math.max(min, Math.min(max, pct));
    setStartPct(splitEl, pct);
  };
  const end = () => {
    gutter.removeEventListener("pointermove", move);
    gutter.removeEventListener("pointerup", end);
    gutter.removeEventListener("pointercancel", end);
    if (captured) {
      try { gutter.releasePointerCapture(e.pointerId); } catch { /* noop */ }
    }
  };

  gutter.addEventListener("pointermove", move);
  gutter.addEventListener("pointerup", end);
  gutter.addEventListener("pointercancel", end);
}

function onKeyDown(e, splitEl) {
  const vertical = isVertical(splitEl);
  let dir = 0;
  if (!vertical) {
    if (e.key === "ArrowLeft") dir = -1;
    else if (e.key === "ArrowRight") dir = 1;
  } else {
    if (e.key === "ArrowUp") dir = -1;
    else if (e.key === "ArrowDown") dir = 1;
  }
  if (!dir) return;
  e.preventDefault();
  const { min, max } = clampBoundsPct(splitEl, vertical);
  let pct = currentStartPct(splitEl, vertical) + dir * KEYBOARD_STEP_PCT;
  pct = Math.max(min, Math.min(max, pct));
  setStartPct(splitEl, pct);
}

/**
 * Initialize all splitters under `root`. Idempotent per element.
 * @param {Document|Element} root
 * @returns {Function} destroy() that detaches all listeners created here.
 */
export function initSplitters(root = document) {
  const cleanups = [];
  root.querySelectorAll("[data-split]").forEach((splitEl) => {
    if (splitEl.__mhSplitWired) return;
    splitEl.__mhSplitWired = true;

    splitEl.querySelectorAll(":scope > .split-gutter").forEach((gutter) => {
      const down = (e) => onPointerDown(e, splitEl, gutter);
      const key = (e) => onKeyDown(e, splitEl);
      gutter.addEventListener("pointerdown", down);
      gutter.addEventListener("keydown", key);
      cleanups.push(() => {
        gutter.removeEventListener("pointerdown", down);
        gutter.removeEventListener("keydown", key);
      });
    });
  });
  return function destroy() {
    cleanups.forEach((fn) => fn());
    root.querySelectorAll("[data-split]").forEach((s) => { s.__mhSplitWired = false; });
  };
}
