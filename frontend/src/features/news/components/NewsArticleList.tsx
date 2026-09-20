import { AsyncStateView } from "@/components/ui";
import type { ApiError } from "@/types";
import { articleSnippet, formatRelativeTime } from "../format";
import type { NewsArticle, NewsSentiment } from "../types";

interface Props {
  articles: NewsArticle[];
  sentimentById: Map<string, NewsSentiment>;
  selectedId?: string;
  onSelect: (id: string) => void;
  isLoading: boolean;
  isError: boolean;
  error?: ApiError;
  onRetry: () => void;
}

function sentimentClass(label: string): string {
  if (label === "positive") return "pos";
  if (label === "negative") return "neg";
  return "neutral";
}

// Middle pane: the scrollable article list. Each row shows source, relative
// time, title, snippet, and a sentiment chip when available. Selection is
// keyboard-navigable from the parent.
export function NewsArticleList({
  articles,
  sentimentById,
  selectedId,
  onSelect,
  isLoading,
  isError,
  error,
  onRetry,
}: Props) {
  if (isLoading) {
    return (
      <div className="news-pane">
        <AsyncStateView status="loading" loadingLabel="Loading articles…" />
      </div>
    );
  }
  if (isError) {
    return (
      <div className="news-pane">
        <AsyncStateView status="error" error={error as ApiError} onRetry={onRetry} />
      </div>
    );
  }
  if (articles.length === 0) {
    return (
      <div className="news-pane news-article-pane">
        <div className="news-pane-header">
          <h2>Articles</h2>
        </div>
        <p className="muted news-empty">No articles match the current filters.</p>
      </div>
    );
  }

  return (
    <div className="news-pane news-article-pane">
      <div className="news-pane-header">
        <h2>Articles</h2>
        <span className="muted">{articles.length}</span>
      </div>
      <ul className="news-article-list">
        {articles.map((a) => {
          const s = sentimentById.get(a.item_id);
          const when = a.type === "rss" ? a.published : a.created_utc;
          return (
            <li key={a.item_id}>
              <button
                type="button"
                className={`news-article-row ${selectedId === a.item_id ? "is-active" : ""}`}
                onClick={() => onSelect(a.item_id)}
              >
                <div className="news-article-row-top">
                  <span className="chip chip-muted">{a.source_name}</span>
                  {s ? (
                    <span className={`chip ${sentimentClass(s.sentiment)}`}>
                      {s.sentiment} {s.score.toFixed(2)}
                    </span>
                  ) : null}
                  <span className="muted news-article-time">{formatRelativeTime(when)}</span>
                </div>
                <div className="news-article-title">{a.title}</div>
                <p className="news-article-snippet">{articleSnippet(a)}</p>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
