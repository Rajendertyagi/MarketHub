// Domain types for the Sentiment dashboard. Reuses the canonical News/Sentiment
// backend (same endpoint News uses) — React only aggregates and renders; all
// scoring lives in the backend (NewsService).
export type {
  NewsArticle,
  NewsFilters,
  NewsSentiment,
  NewsSentimentResponse,
  NewsSource,
} from "@/features/news/types";

export interface SentimentSummary {
  avg: number;
  count: number;
  pos: number;
  neu: number;
  neg: number;
  label: "Bullish" | "Bearish" | "Neutral" | "No data";
}

export interface SentimentGroup {
  key: string;
  n: number;
  pos: number;
  neu: number;
  neg: number;
  avg: number;
}
