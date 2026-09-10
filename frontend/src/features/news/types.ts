// Domain types for the News / Sentiment feature. These mirror the canonical
// backend contracts in api/news_routes.py — React only renders; all scoring,
// aggregation, and ingestion live in the backend (NewsService).

export type NewsSourceType = "rss" | "reddit";

interface NewsArticleBase {
  item_id: string;
  source_id: string;
  source_name: string;
  title: string;
  author: string | null;
}

export interface RssArticle extends NewsArticleBase {
  type: "rss";
  link: string;
  published: string | null;
  summary: string | null;
}

export interface RedditArticle extends NewsArticleBase {
  type: "reddit";
  subreddit: string;
  score: number | null;
  num_comments: number | null;
  url: string;
  permalink: string;
  created_utc: string | null;
  selftext: string | null;
  upvote_ratio: number | null;
}

export type NewsArticle = RssArticle | RedditArticle;

export interface NewsSentiment {
  item_id: string;
  sentiment: string;
  score: number;
  matched_keywords: string[];
}

export interface NewsSource {
  source_id: string;
  name: string;
  source_type: NewsSourceType;
  category: string;
  enabled: boolean;
  config_json: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
}

export interface NewsFilters {
  source_ids?: string[];
  categories?: string[];
  keywords_include?: string[];
  keywords_exclude?: string[];
  symbol?: string;
  max_age_hours?: number;
  limit?: number;
}

export interface NewsSourceInput {
  source_id: string;
  name: string;
  source_type: NewsSourceType;
  category: string;
  enabled: boolean;
  config_json: Record<string, unknown>;
}

export interface NewsListResponse {
  status: string;
  count: number;
  sources_queried: string[];
  articles: NewsArticle[];
}

export interface NewsSentimentResponse {
  status: string;
  count: number;
  sources_queried: string[];
  articles: NewsArticle[];
  sentiments: NewsSentiment[];
}

export interface NewsSourcesResponse {
  status: string;
  sources: NewsSource[];
}

export interface NewsRefreshResponse {
  status: string;
  [key: string]: unknown;
}

export interface NewsSourceActionResponse {
  status: string;
  source_id?: string;
  enabled?: boolean;
  source?: NewsSource;
  deleted?: string;
  message?: string;
  reachable?: boolean;
}
