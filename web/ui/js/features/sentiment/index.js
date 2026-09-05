/**
 * MarketHub WebUI — Market Sentiment feature (first-class view).
 *
 * Reuses the canonical News/Sentiment backend: GET /api/news/sentiment —
 * the same endpoint News uses for row/reader sentiment scoring. No second
 * sentiment engine is created. Presentation helpers (scoreClass / formatScore
 * / labelText / aggregateSentiment) are imported from the shared News
 * sentiment module so list + reader + this dashboard stay consistent.
 *
 * Shows real stored data: overall sentiment, bullish/bearish/neutral
 * distribution, counts, source/category breakdowns, and recent items, with
 * source / category / symbol / age filters and loading / empty / error /
 * partial states. Refresh uses the existing real /api/news/refresh path.
 */

import { $, esc, escAttr, fmtTs } from "../../utils.js";
import { apiGet, apiPost } from "../../api.js";
import { getNewsSources, loadNewsSources } from "../../sources.js";
import {
  scoreClass, formatScore, labelText, aggregateSentiment,
} from "../news/sentiment.js";

const PAGE_LIMIT = 100;

let _wired = false;
let _inFlight = null;
let _categoryBySource = new Map();
let _filters = { sourceId: "", category: "", symbol: "", maxAgeH: "" };

function buildParams() {
  const p = new URLSearchParams();
  if (_filters.sourceId) p.set("source_ids", _filters.sourceId);
  if (_filters.category) p.set("categories", _filters.category);
  if (_filters.symbol) p.set("symbol", _filters.symbol);
  if (_filters.maxAgeH && !isNaN(Number(_filters.maxAgeH)) && Number(_filters.maxAgeH) > 0) {
    p.set("max_age_hours", String(Number(_filters.maxAgeH)));
  }
  p.set("limit", String(PAGE_LIMIT));
  return p;
}

function setStatus({ loading, error }) {
  const loadEl = $("sentiment-loading");
  const errEl = $("sentiment-error");
  if (loadEl) loadEl.classList.toggle("hidden", !loading);
  if (errEl) {
    if (error) { errEl.textContent = error; errEl.classList.remove("hidden"); }
    else errEl.classList.add("hidden");
  }
}

function syncInputs() {
  const set = (id, v) => {
    const el = $(id);
    if (el && document.activeElement !== el) el.value = v;
  };
  set("sentiment-filter-source", _filters.sourceId);
  set("sentiment-filter-category", _filters.category);
  set("sentiment-filter-symbol", _filters.symbol);
  set("sentiment-filter-max-age", _filters.maxAgeH);
}

function setBar(barId, countId, val, total) {
  const bar = $(barId);
  const cnt = $(countId);
  if (cnt) cnt.textContent = String(val);
  if (bar) bar.style.width = (total ? Math.round((val / total) * 100) : 0) + "%";
}

function render(articles, sentiments, data) {
  const byId = new Map();
  articles.forEach((a, i) => {
    if (a && a.item_id && sentiments[i]) byId.set(a.item_id, sentiments[i]);
  });
  const order = articles.map((a) => a.item_id);
  const agg = aggregateSentiment(order, byId);

  const summaryEl = $("sentiment-summary");
  const bdEl = $("sentiment-breakdowns");
  if (summaryEl) summaryEl.classList.remove("hidden");
  if (bdEl) bdEl.classList.remove("hidden");

  const avgEl = $("sentiment-avg");
  const labelEl = $("sentiment-label");
  const totalEl = $("sentiment-total");
  const srcEl = $("sentiment-sources");
  if (avgEl) avgEl.textContent = agg ? formatScore(agg.avg) : "—";
  if (labelEl) {
    const cls = agg ? (agg.avg > 0 ? "bull" : agg.avg < 0 ? "bear" : "neutral") : "neutral";
    labelEl.className = "ui-badge " + cls;
    labelEl.textContent = agg
      ? (agg.avg > 0 ? "Bullish" : agg.avg < 0 ? "Bearish" : "Neutral")
      : "No data";
  }
  if (totalEl) totalEl.textContent = (data.count ?? articles.length) + " items";
  if (srcEl) srcEl.textContent = (data.sources_queried || []).length + " sources";

  setBar("sentiment-bar-pos", "sentiment-count-pos", agg ? agg.pos : 0, agg ? agg.count : 0);
  setBar("sentiment-bar-neu", "sentiment-count-neu", agg ? agg.neu : 0, agg ? agg.count : 0);
  setBar("sentiment-bar-neg", "sentiment-count-neg", agg ? agg.neg : 0, agg ? agg.count : 0);

  renderSourceBreakdown(articles, byId);
  renderCategoryBreakdown(articles, byId);
  renderItems(articles, byId);

  const emptyEl = $("sentiment-empty");
  if (emptyEl) emptyEl.classList.toggle("hidden", articles.length > 0);
}

function _groupSentiment(articles, byId, keyFn) {
  const groups = new Map();
  articles.forEach((a) => {
    const s = byId.get(a.item_id);
    if (!s || s.score == null) return;
    const key = keyFn(a) || "—";
    let g = groups.get(key);
    if (!g) { g = { n: 0, pos: 0, neu: 0, neg: 0, sum: 0 }; groups.set(key, g); }
    g.n++;
    g.sum += Number(s.score);
    if (s.sentiment === "positive") g.pos++;
    else if (s.sentiment === "negative") g.neg++;
    else g.neu++;
  });
  return groups;
}

function renderSourceBreakdown(articles, byId) {
  const body = $("sentiment-source-body");
  if (!body) return;
  const groups = _groupSentiment(articles, byId, (a) => a.source_name);
  if (!groups.size) {
    body.innerHTML = '<tr><td colspan="6" class="empty-row">No data</td></tr>';
    return;
  }
  body.innerHTML = [...groups.entries()].sort((x, y) => y[1].n - x[1].n).map(([name, g]) => {
    const avg = formatScore(g.sum / g.n);
    return `<tr><td>${esc(name)}</td><td>${g.n}</td>` +
      `<td class="sentiment-pos">${g.pos}</td><td>${g.neu}</td>` +
      `<td class="sentiment-neg">${g.neg}</td><td>${avg}</td></tr>`;
  }).join("");
}

function renderCategoryBreakdown(articles, byId) {
  const body = $("sentiment-category-body");
  if (!body) return;
  const groups = _groupSentiment(articles, byId, (a) => _categoryBySource.get(a.source_id));
  if (!groups.size) {
    body.innerHTML = '<tr><td colspan="6" class="empty-row">No data</td></tr>';
    return;
  }
  body.innerHTML = [...groups.entries()].sort((x, y) => y[1].n - x[1].n).map(([cat, g]) => {
    const avg = formatScore(g.sum / g.n);
    return `<tr><td>${esc(cat)}</td><td>${g.n}</td>` +
      `<td class="sentiment-pos">${g.pos}</td><td>${g.neu}</td>` +
      `<td class="sentiment-neg">${g.neg}</td><td>${avg}</td></tr>`;
  }).join("");
}

function renderItems(articles, byId) {
  const body = $("sentiment-body");
  if (!body) return;
  if (!articles.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty-row">No data</td></tr>';
    return;
  }
  body.innerHTML = articles.slice(0, PAGE_LIMIT).map((a) => {
    const s = byId.get(a.item_id);
    const time = fmtTs(a.published || a.created_utc);
    const link = a.link || a.url || a.permalink;
    const title = link
      ? `<a class="sentiment-title-cell" href="${escAttr(link)}" target="_blank" rel="noopener">${esc(a.title)}</a>`
      : `<span class="sentiment-title-cell">${esc(a.title)}</span>`;
    const badge = s
      ? `<span class="ui-badge ${scoreClass(s.sentiment)}">${esc(formatScore(s.score))} ${esc(labelText(s.sentiment))}</span>`
      : `<span class="ui-badge neutral">n/a</span>`;
    const kw = s && s.matched_keywords && s.matched_keywords.length
      ? esc(s.matched_keywords.slice(0, 6).join(", "))
      : "—";
    return `<tr><td>${esc(time)}</td><td>${esc(a.source_name || a.source_id || "—")}</td>` +
      `<td>${title}</td><td>${badge}</td>` +
      `<td>${s ? esc(formatScore(s.score)) : "—"}</td><td class="sentiment-kw">${kw}</td></tr>`;
  }).join("");
}

async function loadSentiment() {
  setStatus({ loading: true, error: null });
  try {
    const data = await apiGet("/api/news/sentiment?" + buildParams().toString());
    render(data.articles || [], data.sentiments || [], data);
    setStatus({ loading: false, error: null });
  } catch (e) {
    // Surface the error but keep any previously rendered content (partial state).
    setStatus({ loading: false, error: "Failed to load sentiment: " + ((e && e.message) || e) });
  }
}

async function refreshSentiment() {
  const btn = $("sentiment-refresh");
  if (btn) btn.disabled = true;
  try {
    await apiPost("/api/news/refresh", {});
    await loadSentiment();
  } catch (e) {
    setStatus({ loading: false, error: "Refresh failed: " + ((e && e.message) || e) });
  }
  if (btn) btn.disabled = false;
}

function readFiltersFromInputs() {
  const src = $("sentiment-filter-source");
  const cat = $("sentiment-filter-category");
  const sym = $("sentiment-filter-symbol");
  const age = $("sentiment-filter-max-age");
  _filters = {
    sourceId: (src && src.value || "").trim(),
    category: (cat && cat.value || "").trim(),
    symbol: (sym && sym.value || "").trim(),
    maxAgeH: (age && age.value || "").trim(),
  };
}

function applyFilters() {
  readFiltersFromInputs();
  loadSentiment();
}

function clearFilters() {
  _filters = { sourceId: "", category: "", symbol: "", maxAgeH: "" };
  syncInputs();
  loadSentiment();
}

function populateSourceOptions() {
  const sel = $("sentiment-filter-source");
  if (!sel) return;
  const cur = sel.value;
  sel.innerHTML = '<option value="">All Sources</option>';
  _categoryBySource = new Map();
  getNewsSources().forEach((s) => {
    const o = document.createElement("option");
    o.value = s.source_id;
    o.textContent = s.name || s.source_id;
    sel.appendChild(o);
    if (s.category) _categoryBySource.set(s.source_id, s.category);
  });
  sel.value = cur;
}

export async function openSentiment() {
  // Sources power the category breakdown + source filter; reuse the shared
  // News/Sources loader so we never fetch sources a second time.
  try { await loadNewsSources(); } catch { /* sources optional */ }
  populateSourceOptions();
  syncInputs();
  if (_inFlight) return _inFlight;
  _inFlight = loadSentiment().finally(() => { _inFlight = null; });
  return _inFlight;
}

export function initSentimentUI() {
  if (_wired) return;
  _wired = true;
  populateSourceOptions();
  const apply = $("sentiment-apply");
  if (apply) apply.addEventListener("click", applyFilters);
  const clear = $("sentiment-clear");
  if (clear) clear.addEventListener("click", clearFilters);
  const refresh = $("sentiment-refresh");
  if (refresh) refresh.addEventListener("click", refreshSentiment);
  ["sentiment-filter-category", "sentiment-filter-symbol"].forEach((id) => {
    const el = $(id);
    if (el) el.addEventListener("keydown", (e) => { if (e.key === "Enter") applyFilters(); });
  });
}

/** Test seam: reset module state between isolated checks. */
export function __resetSentimentForTests() {
  _wired = false;
  _inFlight = null;
}
