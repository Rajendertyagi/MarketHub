import { Button } from "@/components/ui";
import { articleBody, articleLink, formatRelativeTime } from "../format";
import type { NewsArticle, NewsSentiment } from "../types";

interface Props {
  article?: NewsArticle;
  sentiment?: NewsSentiment;
}

function sentimentClass(label: string): string {
  if (label === "positive") return "pos";
  if (label === "negative") return "neg";
  return "neutral";
}

// Right pane: the reader. Shows the selected article's full content plus its
// persisted sentiment. No scoring or aggregation — render only.
export function NewsReader({ article, sentiment }: Props) {
  if (!article) {
    return (
      <div className="news-pane news-reader-pane">
        <div className="news-pane-header">
          <h2>Reader</h2>
        </div>
        <p className="muted news-empty">Select an article to read it here.</p>
      </div>
    );
  }

  const when = article.type === "rss" ? article.published : article.created_utc;
  const body = articleBody(article);
  const link = articleLink(article);

  return (
    <div className="news-pane news-reader-pane">
      <div className="news-pane-header">
        <h2>Reader</h2>
        {sentiment ? (
          <span className={`chip ${sentimentClass(sentiment.sentiment)}`}>
            {sentiment.sentiment} {sentiment.score.toFixed(2)}
          </span>
        ) : null}
      </div>
      <article className="news-reader-body">
        <h3 className="news-reader-title">
          <a href={link} target="_blank" rel="noreferrer">
            {article.title}
          </a>
        </h3>
        <div className="news-reader-meta muted">
          <span className="chip chip-muted">{article.source_name}</span>
          {article.author ? <span>{article.author}</span> : null}
          {when ? <span>{formatRelativeTime(when)}</span> : null}
        </div>
        {body ? (
          <p className="news-reader-text">{body}</p>
        ) : (
          <p className="muted">No article body available — open the original.</p>
        )}
        {sentiment?.matched_keywords?.length ? (
          <div className="news-reader-keywords">
            <span className="muted">Matched: </span>
            {sentiment.matched_keywords.map((k) => (
              <span key={k} className="chip chip-muted">
                {k}
              </span>
            ))}
          </div>
        ) : null}
        <div className="news-reader-actions">
          <Button variant="primary" onClick={() => window.open(link, "_blank", "noreferrer")}>
            Open original
          </Button>
        </div>
      </article>
    </div>
  );
}
