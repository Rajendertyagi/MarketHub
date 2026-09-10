import type { NewsArticle, NewsSentiment } from "../types";

interface Props {
  articles: NewsArticle[];
  byId: Map<string, NewsSentiment>;
}

// Recent items paired with their persisted sentiment score.
export function SentimentItems({ articles, byId }: Props) {
  if (!articles.length) {
    return <div className="hint">No sentiment data for the current filters.</div>;
  }
  return (
    <div className="panel panel-spaced">
      <div className="panel-header">
        <h2>Recent Items</h2>
      </div>
      <ul className="sentiment-items">
        {articles.slice(0, 50).map((a) => {
          const s = byId.get(a.item_id);
          const cls = s
            ? s.sentiment === "positive"
              ? "pos"
              : s.sentiment === "negative"
                ? "neg"
                : "neutral"
            : "neutral";
          const href =
            "link" in a && a.link ? a.link : "url" in a ? a.url : "#";
          return (
            <li key={a.item_id} className="sentiment-item">
              <span className={`chip ${cls}`}>
                {s ? `${s.sentiment} ${s.score.toFixed(2)}` : "—"}
              </span>
              <a
                className="sentiment-item-title"
                href={href}
                target="_blank"
                rel="noreferrer"
              >
                {a.title}
              </a>
              <span className="muted">{a.source_name}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
