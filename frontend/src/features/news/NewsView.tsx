import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/types";
import { Button } from "@/components/ui";
import { useNewsMutations, useNewsSentiment, useNewsSources } from "./useNews";
import type { NewsFilters, NewsSentiment } from "./types";
import { NewsSourcesPane } from "./components/NewsSourcesPane";
import { NewsArticleList } from "./components/NewsArticleList";
import { NewsReader } from "./components/NewsReader";
import { SourcesManager } from "./components/SourcesManager";

// Three-column RSS-reader layout: Sources+filters | Article list | Reader.
// Reuses the News backend (articles + sentiment from /news/sentiment); React
// only renders. Selection survives filter changes by falling back to the first
// article when the current selection is no longer present.
export function NewsView() {
  const [filters, setFilters] = useState<NewsFilters>({});
  const [selectedId, setSelectedId] = useState<string | undefined>(undefined);
  const [showSources, setShowSources] = useState(false);
  const [newCount, setNewCount] = useState(0);

  const news = useNewsSentiment(filters);
  const sources = useNewsSources();
  const m = useNewsMutations();

  const articles = useMemo(() => news.data?.articles ?? [], [news.data]);
  const sentimentById = useMemo(() => {
    const map = new Map<string, NewsSentiment>();
    (news.data?.sentiments ?? []).forEach((s) => map.set(s.item_id, s));
    return map;
  }, [news.data]);

  const prevIds = useRef<Set<string>>(new Set());
  useEffect(() => {
    const ids = new Set(articles.map((a) => a.item_id));
    if (prevIds.current.size > 0) {
      let fresh = 0;
      ids.forEach((id) => {
        if (!prevIds.current.has(id)) fresh += 1;
      });
      if (fresh > 0) setNewCount(fresh);
    }
    prevIds.current = ids;
  }, [articles]);

  const selected =
    articles.find((a) => a.item_id === selectedId) ?? articles[0];

  const onRefresh = async () => {
    try {
      await m.refresh();
      await news.refetch();
    } catch {
      /* refresh failure must not destroy rendered content */
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    const tag = (document.activeElement?.tagName ?? "").toUpperCase();
    if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    if (articles.length === 0) return;
    e.preventDefault();
    const idx = articles.findIndex((a) => a.item_id === selected?.item_id);
    const next =
      e.key === "ArrowDown"
        ? Math.min(articles.length - 1, idx + 1)
        : Math.max(0, idx - 1);
    const target = articles[next];
    if (target) setSelectedId(target.item_id);
  };

  return (
    <div className="news-reader" onKeyDown={onKeyDown}>
      <div className="news-reader-header">
        <div>
          <h1 className="page-title">News</h1>
          <span className="muted">
            Backend-ingested articles &amp; sentiment — React renders only
          </span>
        </div>
        <div className="control-row">
          <Button variant="primary" onClick={onRefresh}>
            Refresh
          </Button>
          {newCount > 0 ? (
            <button
              className="news-new-pill"
              onClick={() => {
                setNewCount(0);
                setSelectedId(articles[0]?.item_id);
              }}
            >
              {newCount} new article{newCount === 1 ? "" : "s"} — Show
            </button>
          ) : null}
        </div>
      </div>

      <div className="news-reader-layout">
        <NewsSourcesPane
          sources={sources.data?.sources ?? []}
          filters={filters}
          onFilterChange={setFilters}
          onManageClick={() => setShowSources(true)}
        />
        <NewsArticleList
          articles={articles}
          sentimentById={sentimentById}
          selectedId={selected?.item_id}
          onSelect={setSelectedId}
          isLoading={news.isLoading}
          isError={news.isError}
          error={news.error as ApiError}
          onRetry={() => news.refetch()}
        />
        <NewsReader
          article={selected}
          sentiment={selected ? sentimentById.get(selected.item_id) : undefined}
        />
      </div>

      {showSources ? (
        <div
          className="modal-overlay"
          onClick={() => setShowSources(false)}
          role="presentation"
        >
          <div
            className="modal"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-label="Manage news sources"
          >
            <div className="modal-header">
              <h3>Manage News Sources</h3>
              <button
                className="icon-btn"
                aria-label="Close"
                onClick={() => setShowSources(false)}
              >
                ✕
              </button>
            </div>
            <div className="modal-body">
              <SourcesManager />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
