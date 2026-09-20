// Aggregation helpers for the Sentiment dashboard. Pure functions over the
// backend-provided articles + sentiment scores; no scoring, no network.
import type { NewsArticle, NewsSentiment, SentimentGroup, SentimentSummary } from "./types";

export function buildSentimentMap(sentiments: NewsSentiment[]): Map<string, NewsSentiment> {
  return new Map(sentiments.map((s) => [s.item_id, s]));
}

export function aggregateSentiment(
  articles: NewsArticle[],
  byId: Map<string, NewsSentiment>,
): SentimentSummary {
  let sum = 0;
  let count = 0;
  let pos = 0;
  let neu = 0;
  let neg = 0;
  for (const a of articles) {
    const s = byId.get(a.item_id);
    if (!s || s.score == null) continue;
    sum += Number(s.score);
    count += 1;
    if (s.sentiment === "positive") pos += 1;
    else if (s.sentiment === "negative") neg += 1;
    else neu += 1;
  }
  const avg = count ? sum / count : 0;
  const label: SentimentSummary["label"] =
    count === 0 ? "No data" : avg > 0 ? "Bullish" : avg < 0 ? "Bearish" : "Neutral";
  return { avg, count, pos, neu, neg, label };
}

export function groupBySentiment(
  articles: NewsArticle[],
  byId: Map<string, NewsSentiment>,
  keyFn: (a: NewsArticle) => string,
): SentimentGroup[] {
  const groups = new Map<
    string,
    { n: number; sum: number; pos: number; neu: number; neg: number }
  >();
  for (const a of articles) {
    const s = byId.get(a.item_id);
    if (!s || s.score == null) continue;
    const key = keyFn(a) || "—";
    let g = groups.get(key);
    if (!g) {
      g = { n: 0, sum: 0, pos: 0, neu: 0, neg: 0 };
      groups.set(key, g);
    }
    g.n += 1;
    g.sum += Number(s.score);
    if (s.sentiment === "positive") g.pos += 1;
    else if (s.sentiment === "negative") g.neg += 1;
    else g.neu += 1;
  }
  return Array.from(groups.entries())
    .map(([key, g]) => ({
      key,
      n: g.n,
      pos: g.pos,
      neu: g.neu,
      neg: g.neg,
      avg: g.sum / g.n,
    }))
    .sort((a, b) => b.avg - a.avg);
}

// Defensive category accessor — the backend article dict may carry `category`;
// the React NewsArticle union does not declare it, so read it loosely.
export function articleCategory(a: NewsArticle): string {
  const c = (a as NewsArticle & { category?: string }).category;
  return c?.length ? c : "—";
}
