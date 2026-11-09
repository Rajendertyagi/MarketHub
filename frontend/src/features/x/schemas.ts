import { z } from "zod";

// Zod schemas for the X/Twitter contracts. Fail loudly so a
// backend/frontend mismatch surfaces as a typed parse error.

export const xCredStatusSchema = z
  .object({
    configured: z.boolean().optional(),
    auth_state: z.string().nullable().optional(),
    auth_reason: z.string().nullable().optional(),
    auth_at: z.string().nullable().optional(),
  })
  .passthrough();

export const xPollConfigSchema = z
  .object({
    enabled: z.boolean(),
    poll_interval_seconds: z.number(),
    default_limit: z.number(),
    retention_days: z.number(),
    cli_path: z.string().nullable().optional(),
    proxy: z.string().nullable().optional(),
    request_delay: z.number().nullable().optional(),
    updated_at: z.string().nullable().optional(),
  })
  .passthrough();

export const xPollConfigResponseSchema = z
  .object({
    config: xPollConfigSchema,
  })
  .passthrough();

export const xStatusSchema = z
  .object({
    auth: z
      .object({
        state: z.string().nullable().optional(),
        reason: z.string().nullable().optional(),
        at: z.string().nullable().optional(),
      })
      .passthrough()
      .optional(),
    enabled: z.boolean().optional(),
    poll_interval_seconds: z.number().nullable().optional(),
    default_limit: z.number().nullable().optional(),
    cli_path: z.string().nullable().optional(),
    cli_pinned: z.string().nullable().optional(),
    cursor_set: z.boolean().optional(),
    cached_tweets: z.number().optional(),
    last_fetch_at: z.string().nullable().optional(),
    last_new_count: z.number().nullable().optional(),
    last_error: z.string().nullable().optional(),
  })
  .passthrough();

export const xTestConnectionSchema = z
  .object({
    status: z.string().optional(),
    state: z.string().nullable().optional(),
    reason: z.string().nullable().optional(),
    checked_at: z.string().nullable().optional(),
  })
  .passthrough();

export const xTweetSchema = z
  .object({
    id: z.string(),
    handle: z.string().nullable().optional(),
    author_name: z.string().nullable().optional(),
    text: z.string().nullable().optional(),
    url: z.string().nullable().optional(),
    metrics: z.record(z.string(), z.number()).nullable().optional(),
    is_retweet: z.boolean().optional(),
    posted_at: z.string().nullable().optional(),
    fetched_at: z.string().nullable().optional(),
  })
  .passthrough();

export const xTweetsResponseSchema = z
  .object({
    count: z.number().optional(),
    tweets: z.array(xTweetSchema).optional(),
  })
  .passthrough();
