import type { NewsArticle } from "../types";

interface NewsListProps {
  articles: NewsArticle[];
}

// Renders persisted news articles (RSS + Reddit) exactly as the backend
// returns them — no client-side aggregation, no re-scoring.
export function NewsList({ articles }: NewsListProps) {
  if (articles.length === 0) {
    return <span className="muted">No articles match the current filters.</span>;
  }
  return (
    <ul className="news-list">
      {articles.map((article) => (
        <NewsItem key={article.item_id} article={article} />
      ))}
    </ul>
  );
}

function NewsItem({ article }: { article: NewsArticle }) {
  const link = article.type === "rss" ? article.link : article.url;
  const when =
    article.type === "rss" ? article.published : article.created_utc;
  const snippet =
    article.type === "rss" ? article.summary : article.selftext;

  return (
    <li className="news-item">
      <div className="news-item-head">
        <a className="news-item-title" href={link} target="_blank" rel="noreferrer">
          {article.title}
        </a>
        <span className="chip">{article.source_name}</span>
        <span className="chip">{article.type}</span>
      </div>
      <div className="news-item-meta muted">
        {article.author ?? "unknown"}
        {when ? ` · ${when}` : ""}
      </div>
      {snippet ? <p className="news-item-snippet">{snippet}</p> : null}
    </li>
  );
}
