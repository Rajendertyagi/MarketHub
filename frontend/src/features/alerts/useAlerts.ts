// Data hooks for the Price Alerts feature. Thin consumers of the API module;
// all mutations invalidate the ["alerts"] query group. React never evaluates
// conditions or triggers alerts client-side.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  clearAlertHistory,
  createAlert,
  deleteAlert,
  getAlertHistory,
  getAlerts,
  rearmAlert,
  setAlertEnabled,
} from "./api";
import { ALERT_HISTORY_LIMIT, ALERT_REFRESH_MS } from "./constants";
import type { AlertHistoryParams, CreateAlertInput } from "./types";

export function useAlerts() {
  return useQuery({
    queryKey: ["alerts", "list"],
    queryFn: ({ signal }) => getAlerts(signal),
    refetchInterval: ALERT_REFRESH_MS,
  });
}

export function useAlertHistory(params: AlertHistoryParams = {}) {
  return useQuery({
    queryKey: ["alerts", "history", params],
    queryFn: ({ signal }) =>
      getAlertHistory({ limit: ALERT_HISTORY_LIMIT, offset: 0, ...params }, signal),
    refetchInterval: ALERT_REFRESH_MS,
  });
}

export interface AlertMutations {
  create: (input: CreateAlertInput) => Promise<unknown>;
  remove: (id: number) => Promise<unknown>;
  rearm: (id: number) => Promise<unknown>;
  setEnabled: (args: { id: number; enabled: boolean }) => Promise<unknown>;
  clearHistory: (alertId?: number) => Promise<unknown>;
}

export function useAlertMutations(): AlertMutations {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["alerts"] });

  const createMut = useMutation({
    mutationFn: (input: CreateAlertInput) => createAlert(input),
    onSuccess: invalidate,
  });
  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteAlert(id),
    onSuccess: invalidate,
  });
  const rearmMut = useMutation({
    mutationFn: (id: number) => rearmAlert(id),
    onSuccess: invalidate,
  });
  const enableMut = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) => setAlertEnabled(id, enabled),
    onSuccess: invalidate,
  });
  const clearMut = useMutation({
    mutationFn: (alertId?: number) => clearAlertHistory(alertId),
    onSuccess: invalidate,
  });

  return {
    create: (input) => createMut.mutateAsync(input),
    remove: (id) => deleteMut.mutateAsync(id),
    rearm: (id) => rearmMut.mutateAsync(id),
    setEnabled: ({ id, enabled }) => enableMut.mutateAsync({ id, enabled }),
    clearHistory: (alertId) => clearMut.mutateAsync(alertId),
  };
}
