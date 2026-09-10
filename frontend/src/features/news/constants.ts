import type { NewsSourceType } from "./types";

// Canonical, backend-aligned constants. Components must reference these rather
// than inlining literals (single source of truth, no magic strings).
export const NEWS_SOURCE_TYPES: readonly NewsSourceType[] = ["rss", "reddit"] as const;

export const DEFAULT_NEWS_LIMIT = 50;
export const MAX_NEWS_LIMIT = 200;

export const NEWS_TABS = [
  { value: "news", label: "News" },
  { value: "sentiment", label: "Sentiment" },
  { value: "sources", label: "Sources" },
] as const;

export type NewsTab = (typeof NEWS_TABS)[number]["value"];
