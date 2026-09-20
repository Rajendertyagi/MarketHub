// Small formatting helpers for the News reader. Pure, no side effects.
export function formatRelativeTime(iso?: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diffMs = Date.now() - then;
  const min = Math.floor(diffMs / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  const wk = Math.floor(day / 7);
  return `${wk}w ago`;
}

export function articleLink(article: {
  type: "rss" | "reddit";
  link?: string;
  url?: string;
}): string {
  if (article.type === "rss") return article.link ?? "#";
  return article.url ?? "#";
}

export function articleSnippet(article: {
  type: "rss" | "reddit";
  summary?: string | null;
  selftext?: string | null;
}): string {
  const raw = article.type === "rss" ? article.summary : article.selftext;
  if (!raw) return "";
  const text = raw
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > 220 ? `${text.slice(0, 220)}…` : text;
}

export function articleBody(article: {
  type: "rss" | "reddit";
  summary?: string | null;
  selftext?: string | null;
}): string {
  const raw = article.type === "rss" ? article.summary : article.selftext;
  if (!raw) return "";
  return raw
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}
