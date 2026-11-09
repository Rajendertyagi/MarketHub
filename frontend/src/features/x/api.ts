// Thin API layer for the X/Twitter feature. Every call goes through the
// shared `request()` helper — no hardcoded URLs, no direct fetch.
import { request } from "@/api/client";
import { actionResultSchema } from "../settings/schemas";
import {
  xCredStatusSchema,
  xPollConfigResponseSchema,
  xStatusSchema,
  xTestConnectionSchema,
  xTweetsResponseSchema,
} from "./schemas";
import type {
  XCredStatus,
  XPollConfig,
  XPollConfigInput,
  XStatus,
  XTestConnection,
  XTweet,
} from "./types";

const X_SETTINGS = "/settings/x";
const X_CONFIG = "/x/config";
const X_STATUS = "/x/status";
const X_TEST = "/x/test";
const X_TWEETS = "/x/tweets";

export async function getXCredStatus(signal?: AbortSignal): Promise<XCredStatus> {
  return request<XCredStatus>(X_SETTINGS, {
    schema: xCredStatusSchema,
    signal,
  });
}

export async function saveXCredentials(
  authToken: string,
  ct0: string,
  signal?: AbortSignal,
): Promise<unknown> {
  return request(X_SETTINGS, {
    method: "POST",
    body: { auth_token: authToken, ct0 },
    schema: actionResultSchema,
    signal,
  });
}

export async function deleteXCredentials(signal?: AbortSignal): Promise<unknown> {
  return request(X_SETTINGS, { method: "DELETE", signal });
}

export async function getXPollConfig(signal?: AbortSignal): Promise<XPollConfig> {
  const res = await request<{ config: XPollConfig }>(X_CONFIG, {
    schema: xPollConfigResponseSchema,
    signal,
  });
  return res.config;
}

export async function saveXPollConfig(
  input: XPollConfigInput,
  signal?: AbortSignal,
): Promise<XPollConfig> {
  const res = await request<{ config: XPollConfig }>(X_CONFIG, {
    method: "POST",
    body: input,
    schema: xPollConfigResponseSchema,
    signal,
  });
  return res.config;
}

export async function getXStatus(signal?: AbortSignal): Promise<XStatus> {
  return request<XStatus>(X_STATUS, {
    schema: xStatusSchema,
    signal,
  });
}

export async function testXConnection(signal?: AbortSignal): Promise<XTestConnection> {
  return request<XTestConnection>(X_TEST, {
    method: "POST",
    schema: xTestConnectionSchema,
    signal,
  });
}

export async function getXTweets(
  opts?: { limit?: number; handle?: string },
  signal?: AbortSignal,
): Promise<XTweet[]> {
  const params: Record<string, string> = {};
  if (opts?.limit != null) params.limit = String(opts.limit);
  if (opts?.handle) params.handle = opts.handle;
  const res = await request<{ tweets?: XTweet[] }>(X_TWEETS, {
    params,
    schema: xTweetsResponseSchema,
    signal,
  });
  return res.tweets ?? [];
}
