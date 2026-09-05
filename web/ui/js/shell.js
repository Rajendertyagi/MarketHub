/**
 * MarketHub WebUI — application shell: header index strip + bottom status bar.
 *
 * Owns the topbar index tiles, the index multi-select menu, and the
 * persistent status bar. Consumes ONLY existing state paths:
 *   - live quotes via the single market SSE stream (onQuote hook in
 *     market.js; initial values from the shared quotes map populated by
 *     loadInitialQuotes) — no new EventSource, no polling, no broker calls
 *   - SSE connection state via the onSseChange hook (the same events that
 *     drive the existing SSE chips)
 *   - feed state via the onSourcesUpdate hook (the same 10 s poll that
 *     drives the Sources UI) — no duplicate polling
 *   - index metadata via the canonical instrument catalog
 *     (/api/instruments/search?type=INDEX); only supported Indian indices
 *     present in the catalog are exposed
 *
 * The selected-indices preference is a non-secret UI preference persisted in
 * localStorage (same pattern as the theme engine). No credentials/tokens.
 */

import { $, chgClass, escAttr, fmt, nowStr } from "./utils.js";
import { getQuote, onQuote, onSseChange } from "./market.js?v=37";
import { getSourcesSnapshot, onSourcesUpdate } from "./market-sources.js?v=37";

// Catalog tradingsymbol → header label, in stable menu order. Only entries
// actually present in the catalog are exposed (never fabricated).
const SUPPORTED = [
  ["NSE:NIFTY50-INDEX", "NIFTY"],
  ["NSE:NIFTYBANK-INDEX", "BANKNIFTY"],
  ["NSE:FINNIFTY-INDEX", "FINNIFTY"],
  ["BSE:SENSEX-INDEX", "SENSEX"],
  ["BSE:BANKEX-INDEX", "BANKEX"],
  ["NSE:MIDCPNIFTY-INDEX", "MIDCPNIFTY"],
  ["NSE:INDIAVIX-INDEX", "INDIA VIX"],
  ["NSE:NIFTYNXT50-INDEX", "NIFTYNXT50"],
];

const STORAGE_KEY = "mh.header-indices.v1";
const DEFAULT_SELECTION = ["NIFTY", "BANKNIFTY"];
const STALE_MIN = 5;            // same freshness threshold as market.js

let registry = [];              // [{label, exchange, tradingsymbol, key}]
let selected = [...DEFAULT_SELECTION];
let sseConnected = false;
let lastQuoteAt = 0;
let lastMarketKey = "";
let shellInitDone = false;

// ── Index discovery (canonical catalog only) ────────────────────────────

async function discover() {
  const found = new Map();
  try {
    const res = await fetch("/api/instruments/search?type=INDEX&limit=100");
    const data = await res.json();
    for (const r of data.results || []) found.set(r.tradingsymbol, r);
  } catch { /* catalog unreachable — strip stays honest below */ }
  // Top up any supported symbol missing from the first page.
  for (const [ts] of SUPPORTED) {
    if (found.has(ts)) continue;
    try {
      const base = ts.split(":")[1].split("-")[0];
      const res = await fetch(
        "/api/instruments/search?type=INDEX&limit=10&q=" +
        encodeURIComponent(base));
      const data = await res.json();
      for (const r of data.results || []) {
        if (r.tradingsymbol === ts) { found.set(ts, r); break; }
      }
    } catch { /* leave absent — never fabricate */ }
  }
  registry = [];
  for (const [ts, label] of SUPPORTED) {
    const r = found.get(ts);
    if (!r) continue;
    registry.push({
      label,
      exchange: r.exchange,
      tradingsymbol: r.tradingsymbol,
      // Quote composite key (exchange:instrument_token); Fyers index ticks
      // carry the exchange-prefixed symbol as instrument_token.
      key: r.exchange + ":" + r.tradingsymbol,
    });
  }
}

// ── Selection persistence (non-secret UI preference) ────────────────────

function loadSelection() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
  } catch { /* corrupt — fall through to default */ }
  const labels = new Set(registry.map((r) => r.label));
  if (Array.isArray(saved)) {
    const valid = saved.filter((l) => labels.has(l));
    if (valid.length) {
      selected = valid;
      return;
    }
  }
  selected = DEFAULT_SELECTION.filter((l) => labels.has(l));
  if (!selected.length) selected = registry.slice(0, 2).map((r) => r.label);
}

function saveSelection() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(selected));
  } catch { /* storage may be unavailable; selection holds for session */ }
}

function orderSelected() {
  const order = new Map(SUPPORTED.map(([ts, label], i) => [label, i]));
  selected.sort((a, b) => (order.get(a) ?? 99) - (order.get(b) ?? 99));
}

// ── Index strip ─────────────────────────────────────────────────────────

function paintTile(tile, q) {
  const valEl = tile.querySelector(".idx-val");
  const chgEl = tile.querySelector(".idx-chg");
  if (!valEl || !chgEl) return;
  if (q == null || q.ltp == null) {
    // Honest unavailable state — never fabricate values.
    valEl.textContent = "—";
    chgEl.textContent = "No quote";
    chgEl.className = "idx-chg stale";
    tile.classList.add("is-stale");
    return;
  }
  tile.classList.remove("is-stale");
  valEl.textContent = fmt(q.ltp, 0);
  const chg = q.change ?? 0;
  const arrow = chg > 0 ? "▲" : chg < 0 ? "▼" : "•";
  const pct = q.change_percent != null ? fmt(q.change_percent) + "%" : "—";
  const sign = chg > 0 ? "+" : "";
  chgEl.textContent = `${arrow} ${sign}${fmt(chg, 0)} (${sign}${pct})`;
  chgEl.className = "idx-chg " + chgClass(chg);
}

function renderStrip() {
  const strip = $("index-strip");
  if (!strip) return;
  strip.innerHTML = "";
  if (!registry.length) {
    const hint = document.createElement("span");
    hint.className = "idx-empty";
    hint.textContent = "Index catalog unavailable";
    strip.appendChild(hint);
    return;
  }
  // The strip sizes to content and scrolls — every selected index stays
  // visible, no hardcoded visible-count cap.
  const visible = selected;
  if (!visible.length) {
    const hint = document.createElement("span");
    hint.className = "idx-empty";
    hint.textContent = "No indices selected";
    strip.appendChild(hint);
    return;
  }
  const byLabel = new Map(registry.map((r) => [r.label, r]));
  for (const label of visible) {
    const meta = byLabel.get(label);
    if (!meta) continue;
    const tile = document.createElement("span");
    tile.className = "idx-tile";
    tile.dataset.key = meta.key;
    tile.title = escAttr(meta.exchange + ":" + meta.tradingsymbol);
    const sym = document.createElement("span");
    sym.className = "idx-sym";
    sym.textContent = label;
    const quote = document.createElement("span");
    quote.className = "idx-quote";
    const val = document.createElement("span");
    val.className = "idx-val";
    val.textContent = "—";
    const chg = document.createElement("span");
    chg.className = "idx-chg stale";
    chg.textContent = "No quote";
    quote.append(val, chg);
    tile.append(sym, quote);
    strip.appendChild(tile);
    paintTile(tile, getQuote(meta.key));
  }
}

function updateTileForQuote(key, q) {
  const strip = $("index-strip");
  if (!strip) return;
  const tile = strip.querySelector(`[data-key="${CSS.escape(key)}"]`);
  if (tile) paintTile(tile, q);
}

// ── Index menu (multi-select) ───────────────────────────────────────────

function renderMenu() {
  const menu = $("index-menu");
  if (!menu) return;
  menu.innerHTML = "";
  const sel = new Set(selected);
  if (!registry.length) {
    const empty = document.createElement("div");
    empty.className = "index-menu-empty";
    empty.textContent = "No indices in catalog";
    menu.appendChild(empty);
    return;
  }
  for (const meta of registry) {
    const item = document.createElement("label");
    item.className = "index-menu-item";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = sel.has(meta.label);
    box.dataset.idx = meta.label;
    const name = document.createElement("span");
    name.className = "index-menu-name";
    name.textContent = meta.label;
    const tick = document.createElement("span");
    tick.className = "index-menu-key";
    tick.textContent = meta.exchange + ":" + meta.tradingsymbol;
    item.append(box, name, tick);
    menu.appendChild(item);
  }
}

function setMenuOpen(open) {
  const menu = $("index-menu");
  const btn = $("index-menu-btn");
  if (!menu || !btn) return;
  menu.classList.toggle("hidden", !open);
  btn.setAttribute("aria-expanded", open ? "true" : "false");
}

function initMenu() {
  const btn = $("index-menu-btn");
  const menu = $("index-menu");
  if (!btn || !menu) return;
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    setMenuOpen(menu.classList.contains("hidden"));
  });
  menu.addEventListener("change", (e) => {
    const box = e.target.closest("input[type=checkbox][data-idx]");
    if (!box) return;
    const label = box.dataset.idx;
    if (box.checked) {
      if (!selected.includes(label)) selected.push(label);
    } else {
      selected = selected.filter((l) => l !== label);
    }
    orderSelected();
    saveSelection();
    renderStrip();
    renderMenu();
    // Keep focus context: reopen state retained, menu stays open.
    setMenuOpen(true);
  });
  document.addEventListener("click", (e) => {
    const wrap = $("index-menu-wrap");
    if (wrap && !wrap.contains(e.target)) setMenuOpen(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setMenuOpen(false);
  });
}

// ── Status bar ──────────────────────────────────────────────────────────

function setFeed(el, state) {
  if (!el) return;
  el.className = "state-chip";
  if (state == null || state.missing) {
    // null: first poll hasn't landed yet; missing: feed not configured.
    el.textContent = state == null ? "…" : "Not configured";
    return;
  }
  const st = state.state || "unknown";
  if (st === "streaming") {
    el.textContent = "Streaming";
    el.classList.add("is-on");
  } else if (st === "connecting" || st === "authorizing" ||
             st === "reconnecting") {
    el.textContent = state.state === "reconnecting"
      ? "Reconnecting" : state.state === "authorizing"
        ? "Authorizing" : "Connecting";
    el.classList.add("is-warn");
  } else if (st === "failed") {
    el.textContent = "Failed";
    el.classList.add("is-off");
  } else if (st === "auth_required") {
    el.textContent = "Login Required";
  } else if (st === "stopped") {
    el.textContent = "Stopped";
  } else {
    el.textContent = st;
  }
}

function renderFeeds(sources) {
  if (!Array.isArray(sources) || !sources.length) {
    setFeed($("sb-upstox"), null);
    setFeed($("sb-fyers"), null);
    return;
  }
  setFeed($("sb-upstox"),
    sources.find((s) => s.name === "upstox") || { missing: true });
  setFeed($("sb-fyers"),
    sources.find((s) => s.name === "fyers") || { missing: true });
}

function renderMarket() {
  const el = $("sb-market");
  if (!el) return;
  const ageMin = lastQuoteAt ? (Date.now() - lastQuoteAt) / 60000 : 999;
  let key, text, cls;
  if (!sseConnected) {
    key = "off"; text = "Offline"; cls = "state-chip is-off";
  } else if (ageMin > STALE_MIN) {
    key = "stale"; text = "Stale"; cls = "state-chip is-warn";
  } else {
    key = "live"; text = "Live"; cls = "state-chip is-on";
  }
  if (key !== lastMarketKey) {
    el.textContent = text;
    el.className = cls;
    lastMarketKey = key;
  }
}

function renderUpdated(data) {
  const el = $("sb-updated");
  if (!el) return;
  const lastMs = Date.parse((data && data.received_ts) || "") || 0;
  const ageMin = lastMs ? (Date.now() - lastMs) / 60000 : 999;
  el.textContent = "Last update " +
    (ageMin > STALE_MIN ? "[STALE] " : "") + nowStr();
}

// ── Init ────────────────────────────────────────────────────────────────

export function initShell() {
  if (shellInitDone) return;
  shellInitDone = true;

  // Register stream/state listeners synchronously so the initial snapshot
  // replay (loadInitialQuotes) and the first sources poll reach the shell.
  onQuote((key, data) => {
    lastQuoteAt = Date.now();
    updateTileForQuote(key, data);
    renderUpdated(data);
    renderMarket();
  });
  onSseChange((connected) => {
    sseConnected = connected;
    renderMarket();
  });
  onSourcesUpdate((sources) => {
    renderFeeds(sources);
    renderMarket();
  });

  initMenu();
  renderFeeds(getSourcesSnapshot());

  // One-shot catalog discovery (no polling); then paint strip + menu.
  discover().then(() => {
    loadSelection();
    renderStrip();
    renderMenu();
  });
}
