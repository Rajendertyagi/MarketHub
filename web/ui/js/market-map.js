/**
 * MarketHub WebUI — Market Map.
 *
 * Stock-level, sector-grouped whole-market visual built on the EXISTING
 * canonical foundation: the same /api/market/map backend projection that reuses
 * the Breadth/Sector-Heatmap universe resolver + sector classifier + canonical
 * quote reader. This module only renders; no aggregation happens in the browser.
 *
 * Tile click reuses the EXISTING F&O stock workspace (switchView + openStock)
 * for F&O underlyings, and honestly declines for non-derivative stocks.
 *
 * Refresh: identical proven strategy as Breadth/Heatmap — a single debounced
 * timer, an in-flight guard (no overlapping requests), self-clearing when the
 * view is inactive, so rapid navigation cannot accumulate timers or loops.
 */

import { apiGet } from "./api.js";
import { switchView } from "./router.js";
import { openStock } from "./fno.js";

const $ = (id) => document.getElementById(id);

let _bound = false;
let _timer = null;
let _current = null;       // last snapshot
let _inflight = false;     // guards against overlapping refresh requests
const MAX_TILES = 600;     // bounded render for very large universes (NSE_EQ)

function _num(v, dash = "—") {
  if (v === null || v === undefined || (typeof v === "number" && Number.isNaN(v))) return dash;
  return v;
}

function _fmtPct(v) {
  if (v === null || v === undefined) return "—";
  return (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
}

// Symmetric, explicit color scale keyed on change %. Unavailable is distinct.
function _colorClass(status, pct) {
  if (status === "unavailable") return "mm-unavailable";
  if (pct == null) return "mm-flat";
  if (pct >= 2) return "mm-strong-up";
  if (pct >= 0.5) return "mm-up";
  if (pct > -0.5) return "mm-flat";
  if (pct > -2) return "mm-down";
  return "mm-strong-down";
}

function _statusWord(status) {
  return ({
    advance: "advance", decline: "decline", unchanged: "unchanged",
    unavailable: "no quote",
  })[status] || status;
}

function _tile(stock, sizeMode) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "mm-tile " + _colorClass(stock.status, stock.change_percent);
  btn.dataset.symbol = stock.symbol;
  const pct = stock.change_percent;
  const chgTxt = stock.status === "unavailable" ? "N/A" : _fmtPct(pct);
  btn.setAttribute(
    "aria-label",
    `${stock.symbol}, sector ${stock.sector}, change ${chgTxt}, ${_statusWord(stock.status)}`,
  );
  // Size by volume (bounded) when requested; equal is the default.
  if (sizeMode === "volume" && stock.volume && stock.volume > 0) {
    const w = Math.max(96, Math.min(220, 96 + Math.log10(stock.volume) * 22));
    btn.style.flex = `0 0 ${w}px`;
  }
  const sym = document.createElement("span");
  sym.className = "mm-sym";
  sym.textContent = stock.symbol;
  const chg = document.createElement("span");
  chg.className = "mm-chg";
  chg.textContent = chgTxt;
  btn.appendChild(sym);
  btn.appendChild(chg);
  btn.addEventListener("click", () => _onTileClick(stock));
  return btn;
}

function _onTileClick(stock) {
  if (stock.fno) {
    switchView("fno");
    openStock(stock.symbol);
  } else {
    const msg = $("mm-message");
    if (msg) msg.textContent =
      `${stock.symbol} has no listed derivatives — open the Instruments page to inspect it.`;
  }
}

function _renderMap(snap) {
  const map = $("mm-map");
  if (!map) return;
  const sizeMode = ($("mm-size")?.value) || "equal";
  const sectorFilter = ($("mm-sector")?.value) || "";
  const movement = ($("mm-movement")?.value) || "all";
  const q = (($("mm-search")?.value) || "").trim().toUpperCase();

  // Populate sector filter options once.
  const sel = $("mm-sector");
  if (sel && !sel.dataset.populated) {
    for (const sg of snap.sectors) {
      if (sg.sector === "Unclassified") continue;
      const opt = document.createElement("option");
      opt.value = sg.sector;
      opt.textContent = sg.sector;
      sel.appendChild(opt);
    }
    sel.dataset.populated = "1";
  }

  // Collect + filter stocks.
  let all = [];
  for (const sg of snap.sectors) {
    for (const st of sg.stocks) all.push(st);
  }
  const filtered = all.filter((st) => {
    if (sectorFilter && st.sector !== sectorFilter) return false;
    if (movement !== "all" && st.status !== movement) return false;
    if (q && !st.symbol.toUpperCase().includes(q)) return false;
    return true;
  });

  const truncated = filtered.length > MAX_TILES;
  const shown = truncated ? filtered.slice(0, MAX_TILES) : filtered;

  // Group shown stocks by sector for rendering.
  const bySector = new Map();
  for (const st of shown) {
    if (!bySector.has(st.sector)) bySector.set(st.sector, []);
    bySector.get(st.sector).push(st);
  }

  map.innerHTML = "";
  const ordered = [...bySector.keys()].sort((a, b) => {
    if (a === "Unclassified") return 1;
    if (b === "Unclassified") return -1;
    return a.localeCompare(b);
  });
  for (const sec of ordered) {
    const wrap = document.createElement("div");
    wrap.className = "mm-sector";
    const title = document.createElement("div");
    title.className = "mm-sector-title";
    const count = bySector.get(sec).length;
    title.textContent = `${sec} (${count})`;
    wrap.appendChild(title);
    const tiles = document.createElement("div");
    tiles.className = "mm-tiles";
    for (const st of bySector.get(sec)) tiles.appendChild(_tile(st, sizeMode));
    wrap.appendChild(tiles);
    map.appendChild(wrap);
  }

  const note = $("mm-note");
  if (note) {
    note.textContent = truncated
      ? `Showing ${MAX_TILES} of ${filtered.length} matching stocks — refine Sector/Movement/Search for a denser view.`
      : "";
  }
}

function _renderSummary(snap) {
  const el = $("mm-summary");
  if (!el) return;
  const chip = (cls, label, value) =>
    `<div class="breadth-stat ${cls}"><span class="label">${label}</span>` +
    `<span class="value">${_num(value)}</span></div>`;
  el.innerHTML =
    chip("", "Eligible", snap.eligible) +
    chip("", "Quoted", snap.quoted) +
    chip("", "Unavailable", snap.unavailable) +
    chip("adv", "Advances", snap.advances) +
    chip("dec", "Declines", snap.declines) +
    chip("", "Unchanged", snap.unchanged) +
    chip("", "Sectors", snap.sectors.length) +
    chip("", "Unclassified", snap.unclassified);
  const fr = $("mm-freshness");
  if (fr) {
    const label = snap.stale ? "stale (last session)" : "as of";
    fr.textContent = `${label} ${snap.as_of ? snap.as_of.replace("T", " ").slice(0, 19) : ""}`;
    fr.className = "chip " + (snap.stale ? "chip-off" : "chip-on");
  }
}

async function loadMap() {
  if (_inflight) return;
  _inflight = true;
  const universe = ($("mm-universe")?.value) || "FNO";
  try {
    const s = await apiGet(`/api/market/map?universe=${encodeURIComponent(universe)}`);
    if (s.error) {
      $("mm-message").textContent = s.error;
      return;
    }
    $("mm-message").textContent =
      `${s.universe} · ${s.sectors.length} sectors · ` +
      `${s.unclassified} unclassified`;
    _current = s;
    _renderSummary(s);
    _renderMap(s);
  } catch (e) {
    $("mm-message").textContent = `Market Map load failed: ${e.message || e}`;
  } finally {
    _inflight = false;
  }
}

function _startLive() {
  _stopLive();
  _timer = setInterval(() => {
    const active = $("view-market-map")?.classList.contains("active");
    if (!active) { _stopLive(); return; }
    loadMap();
  }, 5000);
}

function _stopLive() {
  if (_timer) { clearInterval(_timer); _timer = null; }
}

export function initMarketMapUI() {
  if (_bound) return;
  _bound = true;
  $("mm-universe")?.addEventListener("change", () => {
    const sel = $("mm-sector");
    if (sel) { sel.dataset.populated = ""; sel.innerHTML = '<option value="">All Sectors</option>'; }
    loadMap();
  });
  $("mm-sector")?.addEventListener("change", () => _current && _renderMap(_current));
  $("mm-movement")?.addEventListener("change", () => _current && _renderMap(_current));
  $("mm-search")?.addEventListener("input", () => _current && _renderMap(_current));
  $("mm-size")?.addEventListener("change", () => _current && _renderMap(_current));
}

export async function openMarketMap() {
  initMarketMapUI();
  await loadMap();
  _startLive();
}
