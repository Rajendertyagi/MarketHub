// Thin API layer for the AI Alerts + MCP Tools feature. Every call goes through
// the shared `request()` helper (owns the /api prefix + Zod validation) — no
// hardcoded URLs, no direct fetch. All endpoints are read-only.
import { request } from "@/api/client";
import {
  aiAlertsResponseSchema,
  consumersResponseSchema,
  triggeredEventsResponseSchema,
} from "./schemas";
import type { AiAlertsResponse, ConsumersResponse, TriggeredEventsResponse } from "./types";

const AI_ALERTS = "/ai-alerts";
const AI_EVENTS = "/ai-alerts/events";
const AI_CONSUMERS = "/ai-alerts/consumers";

export async function getConditionAlerts(signal?: AbortSignal): Promise<AiAlertsResponse> {
  return request<AiAlertsResponse>(AI_ALERTS, {
    schema: aiAlertsResponseSchema,
    signal,
  });
}

export async function getTriggeredEvents(
  limit = 200,
  signal?: AbortSignal,
): Promise<TriggeredEventsResponse> {
  return request<TriggeredEventsResponse>(AI_EVENTS, {
    params: { limit },
    schema: triggeredEventsResponseSchema,
    signal,
  });
}

export async function getConsumers(signal?: AbortSignal): Promise<ConsumersResponse> {
  return request<ConsumersResponse>(AI_CONSUMERS, {
    schema: consumersResponseSchema,
    signal,
  });
}
