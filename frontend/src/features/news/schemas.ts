import { z } from "zod";

export const rssArticleSchema = z.object({
  type: z.literal("rss"),
  item_id: z.string(),
  source_id: z.string(),
  source_name: z.string(),
  title: z.string(),
  link: z.string(),
  published: z.string().nullable(),
  summary: z.string().nullable(),
  author: z.string().nullable(),
});

export const redditArticleSchema = z.object({
  type: z.literal("reddit"),
  item_id: z.string(),
  source_id: z.string(),
  source_name: z.string(),
  subreddit: z.string(),
  title: z.string(),
  score: z.number().nullable(),
  num_comments: z.number().nullable(),
  author: z.string().nullable(),
  url: z.string(),
  permalink: z.string(),
  created_utc: z.string().nullable(),
  selftext: z.string().nullable(),
  upvote_ratio: z.number().nullable(),
});

export const newsArticleSchema = z.discriminatedUnion("type", [
  rssArticleSchema,
  redditArticleSchema,
]);

export const newsSentimentSchema = z.object({
  item_id: z.string(),
  sentiment: z.string(),
  score: z.number(),
  matched_keywords: z.array(z.string()),
});

export const newsSourceSchema = z.object({
  source_id: z.string(),
  name: z.string(),
  source_type: z.enum(["rss", "reddit"]),
  category: z.string(),
  enabled: z.boolean(),
  config_json: z.record(z.unknown()),
  created_at: z.string().nullable(),
  updated_at: z.string().nullable(),
});

export const newsListSchema = z.object({
  status: z.string().optional(),
  count: z.number(),
  sources_queried: z.array(z.string()),
  articles: z.array(newsArticleSchema),
});

export const newsSentimentResponseSchema = newsListSchema.extend({
  sentiments: z.array(newsSentimentSchema),
});

export const newsSourcesSchema = z.object({
  status: z.string().optional(),
  sources: z.array(newsSourceSchema),
});
