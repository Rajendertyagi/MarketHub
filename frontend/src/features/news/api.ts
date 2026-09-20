// Thin API layer for the News / Sentiment feature. Every call goes through the
// shared `request()` helper (which owns the /api prefix and Zod validation) —
// no hardcoded URLs, no direct fetch, no client-side data processing.
import { request } from "@/api/client";
import { newsListSchema, newsSentimentResponseSchema, newsSourcesSchema } from "./schemas";
import type {
  NewsFilters,
  NewsListResponse,
  NewsRefreshResponse,
  NewsSentimentResponse,
  NewsSource,
  NewsSourceActionResponse,
  NewsSourceInput,
  NewsSourcesResponse,
  NewsSourceType,
} from "./types";

// Translate a NewsFilters object into flat query params the backend expects.
// Empty arrays are omitted so the backend applies its own defaults.
export function buildNewsQuery(filters: NewsFilters): Record<string, string> {
  const q: Record<string, string> = {};
  if (filters.source_ids?.length) q.source_ids = filters.source_ids.join(",");
  if (filters.categories?.length) q.categories = filters.categories.join(",");
  if (filters.keywords_include?.length) q.keywords_include = filters.keywords_include.join(",");
  if (filters.keywords_exclude?.length) q.keywords_exclude = filters.keywords_exclude.join(",");
  if (filters.symbol) q.symbol = filters.symbol;
  if (filters.max_age_hours != null) q.max_age_hours = String(filters.max_age_hours);
  if (filters.limit != null) q.limit = String(filters.limit);
  return q;
}

export async function getNews(
  filters: NewsFilters = {},
  signal?: AbortSignal,
): Promise<NewsListResponse> {
  return request<NewsListResponse>("/news", {
    params: buildNewsQuery(filters),
    schema: newsListSchema,
    signal,
  });
}

export async function getNewsSentiment(
  filters: NewsFilters = {},
  signal?: AbortSignal,
): Promise<NewsSentimentResponse> {
  return request<NewsSentimentResponse>("/news/sentiment", {
    params: buildNewsQuery(filters),
    schema: newsSentimentResponseSchema,
    signal,
  });
}

export async function refreshNews(
  opts?: { source_ids?: string[]; limit_per_source?: number },
  signal?: AbortSignal,
): Promise<NewsRefreshResponse> {
  return request<NewsRefreshResponse>("/news/refresh", {
    method: "POST",
    body: opts ?? {},
    signal,
  });
}

export async function getNewsSources(signal?: AbortSignal): Promise<NewsSourcesResponse> {
  return request<NewsSourcesResponse>("/news/sources", {
    schema: newsSourcesSchema,
    signal,
  });
}

export async function createNewsSource(
  input: NewsSourceInput,
  signal?: AbortSignal,
): Promise<NewsSourceActionResponse> {
  return request<NewsSourceActionResponse>("/news/sources", {
    method: "POST",
    body: input,
    signal,
  });
}

export async function updateNewsSource(
  sourceId: string,
  input: Partial<NewsSourceInput>,
  signal?: AbortSignal,
): Promise<NewsSourceActionResponse> {
  return request<NewsSourceActionResponse>(`/news/sources/${encodeURIComponent(sourceId)}`, {
    method: "PUT",
    body: input,
    signal,
  });
}

export async function deleteNewsSource(
  sourceId: string,
  signal?: AbortSignal,
): Promise<NewsSourceActionResponse> {
  return request<NewsSourceActionResponse>(`/news/sources/${encodeURIComponent(sourceId)}`, {
    method: "DELETE",
    signal,
  });
}

export async function setNewsSourceEnabled(
  sourceId: string,
  enabled: boolean,
  signal?: AbortSignal,
): Promise<NewsSourceActionResponse> {
  const path = `/news/sources/${encodeURIComponent(sourceId)}/${enabled ? "enable" : "disable"}`;
  return request<NewsSourceActionResponse>(path, { method: "POST", signal });
}

export async function testNewsSource(
  sourceType: NewsSourceType,
  configJson: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<NewsSourceActionResponse> {
  return request<NewsSourceActionResponse>("/news/sources/test", {
    method: "POST",
    body: { source_type: sourceType, config_json: configJson },
    signal,
  });
}

export type { NewsSource };
