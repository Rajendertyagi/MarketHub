// Thin API layer for the Settings feature. Every call goes through the shared
// `request()` helper (owns the /api prefix + Zod validation) — no hardcoded
// URLs, no direct fetch. OAuth redirects are plain navigation functions (they
// hand off to the backend which 302s to the broker), not fetch calls.
import { request } from "@/api/client";
import type {
  AppSettings,
  BackupResult,
  ChatConfigInput,
  ChatStatus,
  FyersSettings,
  NewsSource,
  NewsSourcesResponse,
  SaveResult,
  SourceControlResult,
  SourcesStatusResponse,
  TestSourceInput,
  TestSourceResult,
  UpstoxAuthStatus,
  UpstoxCredStatus,
  UpstoxFeedConfig,
} from "./types";
import {
  actionResultSchema,
  appSettingsSchema,
  chatStatusSchema,
  fyersSettingsSchema,
  newsSourcesResponseSchema,
  sourceControlResultSchema,
  sourcesStatusResponseSchema,
  testSourceResultSchema,
  upstoxAuthStatusSchema,
  upstoxCredStatusSchema,
  upstoxFeedConfigSchema,
} from "./schemas";

// ── Endpoint paths (named constants; no scattered literals in components) ────
const APP_SETTINGS = "/settings/app";
const UPSTOX_AUTH_STATUS = "/auth/upstox/status";
const UPSTOX_TOKEN = "/auth/upstox/token";
const UPSTOX_SESSION = "/auth/upstox/session";
const UPSTOX_CRED = "/settings/upstox";
const UPSTOX_FEED = "/settings/upstox/feed";
const FYERS_CRED = "/settings/fyers";
const FYERS_FEED = "/settings/fyers/feed";
const FYERS_SESSION = "/auth/fyers/session";
const NEWS_SOURCES = "/news/sources";
const SOURCES_STATUS = "/sources/status";
const CHAT_STATUS = "/chat/status";
const CHAT_CONFIG = "/chat/config";
const ADMIN_BACKUP = "/admin/backup";

// ── General application settings ─────────────────────────────────────────────
export async function getAppSettings(signal?: AbortSignal): Promise<AppSettings> {
  return request<AppSettings>(APP_SETTINGS, {
    schema: appSettingsSchema,
    signal,
  });
}

export async function saveAppSettings(
  publicBaseUrl: string,
  signal?: AbortSignal,
): Promise<unknown> {
  return request(APP_SETTINGS, {
    method: "POST",
    body: { public_base_url: publicBaseUrl },
    schema: actionResultSchema,
    signal,
  });
}

// ── Upstox auth + credentials ────────────────────────────────────────────────
export async function getUpstoxAuthStatus(
  signal?: AbortSignal,
): Promise<UpstoxAuthStatus> {
  return request<UpstoxAuthStatus>(UPSTOX_AUTH_STATUS, {
    schema: upstoxAuthStatusSchema,
    signal,
  });
}

// Plain navigation: hand off to the backend OAuth redirect (no fetch).
export function loginWithUpstox(): void {
  window.location.href = "/api/auth/upstox/login";
}

export async function submitUpstoxToken(
  accessToken: string,
  signal?: AbortSignal,
): Promise<unknown> {
  return request(UPSTOX_TOKEN, {
    method: "POST",
    body: { access_token: accessToken },
    signal,
  });
}

export async function forgetUpstoxSession(signal?: AbortSignal): Promise<unknown> {
  return request(UPSTOX_SESSION, { method: "DELETE", signal });
}

export async function getUpstoxCredStatus(
  signal?: AbortSignal,
): Promise<UpstoxCredStatus> {
  return request<UpstoxCredStatus>(UPSTOX_CRED, {
    schema: upstoxCredStatusSchema,
    signal,
  });
}

export async function saveUpstoxCredentials(
  apiKey: string,
  apiSecret: string,
  signal?: AbortSignal,
): Promise<SaveResult> {
  return request<SaveResult>(UPSTOX_CRED, {
    method: "POST",
    body: { api_key: apiKey, api_secret: apiSecret },
    schema: actionResultSchema,
    signal,
  });
}

export async function deleteUpstoxCredentials(
  signal?: AbortSignal,
): Promise<SaveResult> {
  return request<SaveResult>(UPSTOX_CRED, {
    method: "DELETE",
    schema: actionResultSchema,
    signal,
  });
}

export async function getUpstoxFeedConfig(
  signal?: AbortSignal,
): Promise<UpstoxFeedConfig> {
  return request<UpstoxFeedConfig>(UPSTOX_FEED, {
    schema: upstoxFeedConfigSchema,
    signal,
  });
}

export async function saveUpstoxFeedConfig(
  enabled: boolean,
  signal?: AbortSignal,
): Promise<UpstoxFeedConfig> {
  return request<UpstoxFeedConfig>(UPSTOX_FEED, {
    method: "POST",
    body: { enabled },
    schema: upstoxFeedConfigSchema,
    signal,
  });
}

// ── Fyers auth + credentials ─────────────────────────────────────────────────
export function loginWithFyers(): void {
  window.location.href = "/api/auth/fyers/login";
}

export async function getFyersSettings(
  signal?: AbortSignal,
): Promise<FyersSettings> {
  return request<FyersSettings>(FYERS_CRED, {
    schema: fyersSettingsSchema,
    signal,
  });
}

export async function saveFyersCredentials(
  appId: string,
  secretId: string,
  pin?: string,
  signal?: AbortSignal,
): Promise<SaveResult> {
  return request<SaveResult>(FYERS_CRED, {
    method: "POST",
    body: { app_id: appId, secret_id: secretId, pin: pin ?? "" },
    schema: actionResultSchema,
    signal,
  });
}

export async function forgetFyersSession(signal?: AbortSignal): Promise<unknown> {
  return request(FYERS_SESSION, { method: "DELETE", signal });
}

export async function saveFyersFeedConfig(
  enabled: boolean,
  signal?: AbortSignal,
): Promise<SourceControlResult> {
  return request<SourceControlResult>(FYERS_FEED, {
    method: "POST",
    body: { enabled },
    schema: sourceControlResultSchema,
    signal,
  });
}

// ── News sources ─────────────────────────────────────────────────────────────
export async function getNewsSources(
  signal?: AbortSignal,
): Promise<NewsSourcesResponse> {
  return request<NewsSourcesResponse>(NEWS_SOURCES, {
    schema: newsSourcesResponseSchema,
    signal,
  });
}

export async function createNewsSource(
  source: NewsSource,
  signal?: AbortSignal,
): Promise<unknown> {
  return request(NEWS_SOURCES, {
    method: "POST",
    body: source,
    schema: actionResultSchema,
    signal,
  });
}

export async function updateNewsSource(
  sourceId: string,
  source: NewsSource,
  signal?: AbortSignal,
): Promise<unknown> {
  return request(`${NEWS_SOURCES}/${encodeURIComponent(sourceId)}`, {
    method: "PUT",
    body: source,
    signal,
  });
}

export async function deleteNewsSource(
  sourceId: string,
  signal?: AbortSignal,
): Promise<unknown> {
  return request(`${NEWS_SOURCES}/${encodeURIComponent(sourceId)}`, {
    method: "DELETE",
    signal,
  });
}

export async function setNewsSourceEnabled(
  sourceId: string,
  enabled: boolean,
  signal?: AbortSignal,
): Promise<unknown> {
  const action = enabled ? "enable" : "disable";
  return request(
    `${NEWS_SOURCES}/${encodeURIComponent(sourceId)}/${action}`,
    { method: "POST", body: {}, signal },
  );
}

export async function testNewsSource(
  input: TestSourceInput,
  signal?: AbortSignal,
): Promise<TestSourceResult> {
  return request<TestSourceResult>(`${NEWS_SOURCES}/test`, {
    method: "POST",
    body: input,
    schema: testSourceResultSchema,
    signal,
  });
}

// ── Market (broker feed) sources ─────────────────────────────────────────────
export async function getSourcesStatus(
  signal?: AbortSignal,
): Promise<SourcesStatusResponse> {
  return request<SourcesStatusResponse>(SOURCES_STATUS, {
    schema: sourcesStatusResponseSchema,
    signal,
  });
}

export async function controlSource(
  name: string,
  action: "start" | "stop" | "restart",
  signal?: AbortSignal,
): Promise<SourceControlResult> {
  return request<SourceControlResult>(
    `/sources/${encodeURIComponent(name)}/${action}`,
    { method: "POST", schema: sourceControlResultSchema, signal },
  );
}

// Reconnect a broker feed through the source manager (no full restart).
export async function restartBrokerFeed(
  name: string,
  signal?: AbortSignal,
): Promise<SourceControlResult> {
  return request<SourceControlResult>(
    `/sources/${encodeURIComponent(name)}/restart`,
    { method: "POST", schema: sourceControlResultSchema, signal },
  );
}

// ── AI / MCP ─────────────────────────────────────────────────────────────────
export async function getChatStatus(signal?: AbortSignal): Promise<ChatStatus> {
  return request<ChatStatus>(CHAT_STATUS, {
    schema: chatStatusSchema,
    signal,
  });
}

export async function saveChatConfig(
  config: ChatConfigInput,
  signal?: AbortSignal,
): Promise<unknown> {
  return request(CHAT_CONFIG, {
    method: "POST",
    body: config,
    signal,
  });
}

// ── Backup ───────────────────────────────────────────────────────────────────
export async function backupDatabase(signal?: AbortSignal): Promise<BackupResult> {
  return request<BackupResult>(ADMIN_BACKUP, {
    method: "POST",
    signal,
  });
}
