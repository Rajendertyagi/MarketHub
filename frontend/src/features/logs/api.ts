// Thin API layer for the Logs feature. The snapshot uses the shared `request()`
// helper (owns the /api prefix + Zod validation). The live stream uses the
// browser EventSource directly (SSE) — see useLogStream.
import { request } from "@/api/client";
import { LOG_HISTORY_LIMIT } from "./constants";
import { logsResponseSchema } from "./schemas";
import type { LogFilters, LogsResponse } from "./types";

export async function getLogs(
  filters: LogFilters = {},
  signal?: AbortSignal,
): Promise<LogsResponse> {
  return request<LogsResponse>("/logs", {
    params: {
      level: filters.level ?? "",
      logger: filters.logger ?? "",
      search: filters.search ?? "",
      consumer_id: filters.consumer_id ?? "",
      alert_id: filters.alert_id ?? "",
      request_id: filters.request_id ?? "",
      limit: LOG_HISTORY_LIMIT,
    },
    schema: logsResponseSchema,
    signal,
  });
}
