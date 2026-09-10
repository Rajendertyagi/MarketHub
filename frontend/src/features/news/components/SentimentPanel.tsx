import type { NewsArticle, NewsSentiment } from "../types";

interface SentimentPanelProps {
  articles: NewsArticle[];
  sentiments: NewsSentiment[];
}

// Pairs each article with its persisted sentiment score. Sentiment is computed
// and stored by the backend; React only displays it. Unknown items fall back to
// a neutral label rather than fabricating a value.
export function SentimentPanel({ articles, sentiments }: SentimentPanelProps) {
  const byItem = new Map(sentiments.map((s) => [s.item_id, s]));

  if (articles.length === 0) {
    return <span className="muted">No sentiment data for the current filters.</span>;
  }

  return (
    <ul className="news-list">
      {articles.map((article) => {
        const sentiment = byItem.get(article.item_id);
        return (
          <li className="news-item" key={article.item_id}>
            <div className="news-item-head">
              <span className="news-item-title">{article.title}</span>
              {sentiment ? (
                <span className={`chip ${sentimentClass(sentiment.sentiment)}`}>
                  {sentiment.sentiment} {sentiment.score.toFixed(2)}
                </span>
              ) : (
                <span className="chip">unscored</span>
              )}
            </div>
            {sentiment?.matched_keywords?.length ? (
              <div className="news-item-meta muted">
                matched: {sentiment.matched_keywords.join(", ")}
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function sentimentClass(label: string): string {
  const v = label.toLowerCase();
  if (v.includes("positive")) return "chip-on";
  if (v.includes("negative")) return "chip-off";
  return "chip-muted";
}
