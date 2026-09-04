/**
 * MarketHub WebUI — News reader pane (N-UI1).
 *
 * Owns the right pane: title/meta/sentiment/full stored summary/Open
 * Original for the selected article. Summary is rendered as TEXT (the
 * backend contract is plain summary/selftext, not sanitized HTML).
 * Reader scroll resets on selection change, preserved otherwise.
 */

import { $, esc, escAttr } from "../../utils.js";
import { formatScore, labelText, scoreClass } from "./sentiment.js";

function fmtDT(ts) {
  if (!ts) return "";
  try { return new Date(ts).toLocaleString(); } catch { return ""; }
}

function isoTime(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    return isNaN(d.getTime()) ? "" : d.toISOString();
  } catch { return ""; }
}

export function initReaderUI(store, hooks) {
  const readerEl = $("news-reader");
  const shell = $("news-shell");
  if (!readerEl) return {};
  let lastRenderedId = null;

  function renderReader() {
    const a = store.selected();
    const keepScroll = a && a.item_id === lastRenderedId;
    const top = keepScroll ? readerEl.scrollTop : 0;
    if (!a) {
      lastRenderedId = null;
      const msg = store.order.length ? "Select an article to read" : "No article available";
       readerEl.innerHTML = `<div class="ui-callout neutral p-20">${esc(msg)}</div>`;
      return;
    }
    lastRenderedId = a.item_id;
    const isReddit = a.type === "reddit";
    const typeLabel = isReddit ? "Reddit · r/" + (a.subreddit || "?") : "RSS";
    const link = a.link || a.url || a.permalink || "";
    const ts = a.published || a.created_utc;
    const iso = isoTime(ts);
    const timeEl = iso
      ? esc(fmtDT(ts))
      : esc(fmtDT(ts));
    const s = store.sentiment.byId.get(a.item_id);
    const sentHTML = s
      ? `<span class="ui-badge ${scoreClass(s.sentiment)}">` +
        `${esc(formatScore(s.score))} ${esc(labelText(s.sentiment))}</span>` +
        ((s.matched_keywords && s.matched_keywords.length)
          ? `<span class="news-reader-kw">kw: ${esc(s.matched_keywords.join(", "))}</span>` : "")
      : `<span class="news-reader-kw">sentiment unavailable</span>`;
    const summary = a.summary || a.selftext || "";
    readerEl.innerHTML =
      `<div><button type="button" id="news-reader-back" class="ui-btn ui-btn-sm news-reader-back">← Back</button></div>` +
      `<div class="news-reader-type">${esc(typeLabel)}</div>` +
      `<h3 class="news-reader-title">${esc(a.title)}</h3>` +
      `<div class="news-reader-meta">` +
      `<span>${esc(a.source_name || a.source_id || "")}</span>` +
      (timeEl ? `<span>${timeEl}</span>` : "") +
      (isReddit && a.score != null ? `<span>▲ ${a.score}</span>` : "") +
      (isReddit && a.num_comments != null ? `<span>${a.num_comments} comments</span>` : "") +
      (a.author ? `<span>by ${esc(a.author)}</span>` : "") +
      `</div>` +
      `<div class="news-reader-sent">${sentHTML}</div>` +
      `<div class="news-reader-summary"></div>` +
      `<div class="news-reader-actions">` +
      (link ? `<a class="ui-btn ui-btn-sm" href="${escAttr(link)}" target="_blank" rel="noopener">Open Original ↗</a>` : "") +
      `<span class="news-reader-nav">` +
      `<button type="button" class="ui-btn ui-btn-sm" data-news-nav="-1" title="Previous article">‹ Prev</button>` +
      `<button type="button" class="ui-btn ui-btn-sm" data-news-nav="1" title="Next article">Next ›</button>` +
      `</span></div>`;
    const sumEl = readerEl.querySelector(".news-reader-summary");
    if (sumEl) sumEl.textContent = summary || "(no summary stored for this article)";
    if (keepScroll) readerEl.scrollTop = top;
    else readerEl.scrollTop = 0;
  }

  readerEl.addEventListener("click", (e) => {
    const back = e.target && e.target.closest ? e.target.closest("#news-reader-back") : null;
    if (back) {
      if (shell) shell.classList.remove("news-mobile-reader");
      return;
    }
    const nav = e.target && e.target.closest ? e.target.closest("[data-news-nav]") : null;
    if (nav && hooks && hooks.onStep) {
      hooks.onStep(Number(nav.getAttribute("data-news-nav")) || 0);
    }
  });

  store.on("selection", renderReader);
  store.on("articles", renderReader);
  store.on("sentiment", renderReader);

  return { renderReader };
}
