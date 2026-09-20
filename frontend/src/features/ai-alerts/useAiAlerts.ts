// Data hooks for the AI Alerts + MCP Tools feature. Thin consumers of the API
// module; all read-only (no mutations). React never evaluates alerts.
import { useQuery } from "@tanstack/react-query";
import { getConditionAlerts, getConsumers, getTriggeredEvents } from "./api";
import { AI_ALERT_REFRESH_MS, AI_EVENTS_LIMIT } from "./constants";

export function useConditionAlerts() {
  return useQuery({
    queryKey: ["ai-alerts", "list"],
    queryFn: ({ signal }) => getConditionAlerts(signal),
    refetchInterval: AI_ALERT_REFRESH_MS,
  });
}

export function useTriggeredEvents() {
  return useQuery({
    queryKey: ["ai-alerts", "events"],
    queryFn: ({ signal }) => getTriggeredEvents(AI_EVENTS_LIMIT, signal),
    refetchInterval: AI_ALERT_REFRESH_MS,
  });
}

export function useConsumers() {
  return useQuery({
    queryKey: ["ai-alerts", "consumers"],
    queryFn: ({ signal }) => getConsumers(signal),
    refetchInterval: AI_ALERT_REFRESH_MS,
  });
}
