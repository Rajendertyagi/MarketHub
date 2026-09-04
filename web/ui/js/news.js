/**
 * MarketHub WebUI — news articles + sentiment (persisted history).
 *
 * The page opens instantly from SQLite history (no network needed to
 * display cached items). The Refresh button pulls enabled sources via
 * POST /api/news/refresh (persist + dedup server-side) and then reloads
 * history. Filters apply against history.
 */

import { $, esc } from "./utils.js";
import { apiPost } from "./api.js";
import { getNewsSources, loadNewsSources } from "./sources.js";

function _populateSourceFilter() {
  const sel = $("news-filter-source");
  if (!sel) return;
  const val = sel.value;
  sel.innerHTML = '<option value="">All Sources</option>';
  getNewsSources().forEach(s => {
    const opt = document.createElement("option");
    opt.value = s.source_id;
    opt.textContent = s.name;
    sel.appendChild(opt);
  });
  sel.value = val;
}

async function _loadNews() {
  const listEl = $("news-articles-list");
  if (!listEl) return;
  listEl.innerHTML = '<div class="empty-row" style="padding:20px;text-align:center">Loading…</div>';

  const params = new URLSearchParams();
  const src = $("news-filter-source")?.value;
  const kw = $("news-filter-keywords")?.value?.trim();
  const sym = $("news-filter-symbol")?.value?.trim();
  if (src) params.set("source_ids", src);
  if (kw) params.set("keywords_include", kw);
  if (sym) params.set("symbol", sym);
  params.set("limit", "50");

  try {
    const resp = await fetch("/api/news?" + params.toString());
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    if (!data.articles?.length) {
      listEl.innerHTML = '<div class="empty-row" style="padding:20px;text-align:center">No articles found — try Refresh to pull the latest from your sources</div>';
      return;
    }
    listEl.innerHTML = "";
    data.articles.forEach(a => {
      const div = document.createElement("div");
      div.className = "news-article";
      const typeBadge = a.type === "rss" ? "RSS" : "r/" + (a.subreddit || "?");
      const timeStr = a.published || a.created_utc || "";
      const link = a.link || a.url || "#";
      div.innerHTML = `
        <span class="news-article-type">${esc(typeBadge)}</span>
        <div class="news-article-body">
          <div class="news-article-title"><a href="${esc(link)}" target="_blank" rel="noopener">${esc(a.title)}</a></div>
          <div class="news-article-meta">
            <span>${esc(a.source_name || "")}</span>
            <span>${esc(timeStr ? new Date(timeStr).toLocaleString() : "")}</span>
            ${a.score != null ? `<span>▲ ${a.score}</span>` : ""}
            ${a.num_comments != null ? `<span>${a.num_comments} comments</span>` : ""}
          </div>
          ${a.summary ? `<div class="news-article-summary">${esc(a.summary.substring(0, 200))}</div>` : ""}
        </div>`;
      listEl.appendChild(div);
    });
  } catch (e) {
    listEl.innerHTML = `<div class="empty-row" style="padding:20px;text-align:center;color:var(--red)">Error: ${esc(e.message)}</div>`;
  }
}

async function _refreshNews() {
  const btn = $("news-refresh");
  const listEl = $("news-articles-list");
  if (btn) { btn.disabled = true; btn.textContent = "Refreshing…"; }
  try {
    await apiPost("/api/news/refresh", {});
    await _loadNews();
  } catch (e) {
    if (listEl) {
      listEl.innerHTML = `<div class="empty-row" style="padding:12px;text-align:center;color:var(--red)">Refresh failed: ${esc(e.message)}</div>`;
    }
  }
  if (btn) { btn.disabled = false; btn.textContent = "Refresh"; }
}

async function _loadSentiment() {
  const resultEl = $("news-sentiment-result");
  if (!resultEl) return;
  resultEl.innerHTML = '<div class="empty-row" style="padding:20px;text-align:center">Analyzing…</div>';

  const params = new URLSearchParams();
  const src = $("news-filter-source")?.value;
  const kw = $("news-filter-keywords")?.value?.trim();
  if (src) params.set("source_ids", src);
  if (kw) params.set("keywords_include", kw);
  params.set("limit", "30");

  try {
    const resp = await fetch("/api/news/sentiment?" + params.toString());
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    const data = await resp.json();
    if (!data.sentiments?.length) {
      resultEl.innerHTML = '<div class="empty-row" style="padding:20px;text-align:center">No sentiment results</div>';
      return;
    }
    // Aggregate
    const scores = data.sentiments.map(s => s.score);
    const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
    const pos = data.sentiments.filter(s => s.sentiment === "positive").length;
    const neg = data.sentiments.filter(s => s.sentiment === "negative").length;
    const neu = data.sentiments.filter(s => s.sentiment === "neutral").length;

    resultEl.innerHTML = `
      <div class="news-sentiment-header">
        <span style="font-weight:600">Aggregate:</span>
        <span class="news-score-badge ${avg > 0.1 ? 'news-score-positive' : avg < -0.1 ? 'news-score-negative' : 'news-score-neutral'}">
          ${avg > 0 ? "+" : ""}${avg.toFixed(3)}
        </span>
        <span style="font-size:12px;color:var(--text-muted)">${pos} positive, ${neg} negative, ${neu} neutral</span>
      </div>`;
    data.sentiments.forEach((s, i) => {
      const article = data.articles?.[i];
      const title = article?.title || s.item_id;
      const div = document.createElement("div");
      div.className = "news-sentiment-item";
      div.innerHTML = `
        <span class="news-score-badge ${s.sentiment === 'positive' ? 'news-score-positive' : s.sentiment === 'negative' ? 'news-score-negative' : 'news-score-neutral'}">
          ${s.score > 0 ? "+" : ""}${s.score.toFixed(3)}
        </span>
        <span style="flex:1;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(title)}</span>
        <span style="font-size:11px;color:var(--text-muted)">${esc((s.matched_keywords || []).join(", "))}</span>`;
      resultEl.appendChild(div);
    });
  } catch (e) {
    resultEl.innerHTML = `<div class="empty-row" style="padding:20px;text-align:center;color:var(--red)">Error: ${esc(e.message)}</div>`;
  }
}

/**
 * View-enter hook (registered once via the router): sources first so the
 * filter dropdown is populated, then instant history render.
 */
export async function openNews() {
  await loadNewsSources();
  _populateSourceFilter();
  await _loadNews();
}

export function initNewsUI() {
  const refreshBtn = $("news-refresh");
  if (refreshBtn) refreshBtn.addEventListener("click", _refreshNews);
  const sentimentBtn = $("news-sentiment-btn");
  if (sentimentBtn) sentimentBtn.addEventListener("click", _loadSentiment);
}
