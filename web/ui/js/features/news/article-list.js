/**
 * MarketHub WebUI — News dense article list (N-UI1).
 *
 * Owns the center pane: dense rows (title + source · time + sentiment
 * badge), active-row state, list scroll preservation, and list-region
 * loading/empty/error states. Selection goes through the store; the full
 * summary is NEVER rendered here (reader owns it).
 */

import { $, esc, escAttr } from "../../utils.js";
import { formatScore, labelText, scoreClass } from "./sentiment.js";

function fmtTime(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    return sameDay
      ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " +
        d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch { return ""; }
}

function typeBadge(a) {
  return a.type === "reddit" ? "r/" + (a.subreddit || "?") : "RSS";
}

export function initArticleListUI(store) {
  const listEl = $("news-articles-list");
  if (!listEl) return {};

  function rowHTML(a) {
    const id = a.item_id;
    const active = store.selectedItemId === id;
    const s = store.sentiment.byId.get(id);
    const badge = s
      ? `<span class="news-score-badge ${scoreClass(s.sentiment)} news-row-badge"` +
        ` title="${escAttr(labelText(s.sentiment))} ${(s.matched_keywords || []).join(", ")}">` +
        `${esc(formatScore(s.score))}</span>`
      : "";
    const time = fmtTime(a.published || a.created_utc);
    const meta = `${esc(a.source_name || "")}` +
      (time ? ` · ${esc(time)}` : "") +
      (a.score != null ? ` · ▲ ${a.score}` : "") +
      (a.num_comments != null ? ` · ${a.num_comments} comments` : "");
    return `<button type="button" role="option" aria-selected="${active ? "true" : "false"}"` +
      ` class="news-row${active ? " active" : ""}" data-news-item="${escAttr(id)}">` +
      `${badge}<span class="news-row-body"><span class="news-row-title">${esc(a.title)}</span>` +
      `<span class="news-row-meta">${esc(typeBadge(a))} · ${meta}</span></span></button>`;
  }

  function renderList(opts = {}) {
    const keepScroll = !opts.resetScroll;
    const top = keepScroll ? listEl.scrollTop : 0;
    if (store.loading && !store.order.length) {
      listEl.innerHTML = '<div class="empty-row" style="padding:20px;text-align:center">Loading articles…</div>';
      return;
    }
    if (store.error && !store.order.length) {
      listEl.innerHTML = `<div class="empty-row" style="padding:20px;text-align:center;color:var(--red)">Error: ${esc(store.error)}</div>`;
      return;
    }
    if (!store.order.length) {
      listEl.innerHTML = '<div class="empty-row" style="padding:20px;text-align:center">' +
        (store.loadedOnce ? "No articles match these filters" : "Open News to load articles") + "</div>";
      return;
    }
    listEl.innerHTML = store.order.map((id) => {
      const a = store.articles.get(id);
      return a ? rowHTML(a) : "";
    }).join("");
    if (keepScroll) listEl.scrollTop = top;
    else listEl.scrollTop = 0;
  }

  function currentIndex() {
    return store.order.indexOf(store.selectedItemId);
  }

  listEl.addEventListener("click", (e) => {
    const row = e.target && e.target.closest ? e.target.closest("[data-news-item]") : null;
    if (!row) return;
    store.select(row.getAttribute("data-news-item"));
    // Phone master/detail: reveal the reader pane.
    const shell = $("news-shell");
    if (shell) shell.classList.add("news-mobile-reader");
  });

  // Keyboard minimum (N-UI1): rows are natively focusable buttons;
  // Enter/Space activate via click. ArrowUp/Down move within the list.
  listEl.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const rows = Array.from(listEl.querySelectorAll("[data-news-item]"));
    if (!rows.length) return;
    e.preventDefault();
    const idx = rows.findIndex((r) => r.getAttribute("data-news-item") === store.selectedItemId);
    const next = e.key === "ArrowDown"
      ? rows[Math.min(idx + 1, rows.length - 1)]
      : rows[Math.max(idx - 1, 0)];
    if (next) {
      next.focus();
      store.select(next.getAttribute("data-news-item"));
    }
  });

  store.on("articles", () => renderList({ resetScroll: store.lastScopeReset }));
  store.on("selection", () => {
    // Cheap path: toggle classes without rebuilding rows (keeps focus/scroll).
    const prev = listEl.querySelector(".news-row.active");
    if (prev) {
      prev.classList.remove("active");
      prev.setAttribute("aria-selected", "false");
    }
    if (store.selectedItemId) {
      const row = listEl.querySelector(`[data-news-item="${CSS.escape(store.selectedItemId)}"]`);
      if (row) {
        row.classList.add("active");
        row.setAttribute("aria-selected", "true");
      } else {
        renderList();
      }
    }
  });
  store.on("status", renderList);
  store.on("sentiment", () => renderList());

  return {
    renderList,
    selectIndex(delta) {
      const i = currentIndex();
      const n = i < 0 ? (delta > 0 ? 0 : store.order.length - 1) : i + delta;
      const id = store.order[Math.max(0, Math.min(n, store.order.length - 1))];
      if (id) store.select(id);
    },
  };
}
