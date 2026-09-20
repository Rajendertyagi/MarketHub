// Thin API layer for the Price Alerts feature. Every call goes through the
// shared `request()` helper (owns the /api prefix + Zod validation) — no
// hardcoded URLs, no direct fetch, no client-side evaluation.
import { request } from "@/api/client";
import { alertActionSchema, alertHistoryResponseSchema, alertsResponseSchema } from "./schemas";
import type {
  AlertHistoryParams,
  AlertHistoryResponse,
  AlertsResponse,
  CreateAlertInput,
} from "./types";

// Endpoint paths kept as named constants (single source of truth; no scattered
// string literals in components).
const ALERTS = "/alerts";
const HISTORY = "/alerts/history";

export async function getAlerts(signal?: AbortSignal): Promise<AlertsResponse> {
  return request<AlertsResponse>(ALERTS, {
    schema: alertsResponseSchema,
    signal,
  });
}

export async function createAlert(input: CreateAlertInput, signal?: AbortSignal): Promise<unknown> {
  return request(ALERTS, {
    method: "POST",
    body: input,
    schema: alertActionSchema,
    signal,
  });
}

export async function deleteAlert(id: number, signal?: AbortSignal): Promise<unknown> {
  return request(`${ALERTS}/${id}`, { method: "DELETE", signal });
}

export async function rearmAlert(id: number, signal?: AbortSignal): Promise<unknown> {
  return request(`${ALERTS}/${id}/rearm`, { method: "POST", signal });
}

export async function setAlertEnabled(
  id: number,
  enabled: boolean,
  signal?: AbortSignal,
): Promise<unknown> {
  return request(`${ALERTS}/${id}/enabled`, {
    method: "POST",
    body: { enabled },
    signal,
  });
}

export async function getAlertHistory(
  params: AlertHistoryParams = {},
  signal?: AbortSignal,
): Promise<AlertHistoryResponse> {
  return request<AlertHistoryResponse>(HISTORY, {
    params: {
      limit: params.limit,
      offset: params.offset,
      provider: params.provider ?? "",
      alert_id: params.alert_id,
    },
    schema: alertHistoryResponseSchema,
    signal,
  });
}

export async function clearAlertHistory(alertId?: number, signal?: AbortSignal): Promise<unknown> {
  return request(HISTORY, {
    method: "DELETE",
    params: { alert_id: alertId },
    signal,
  });
}
