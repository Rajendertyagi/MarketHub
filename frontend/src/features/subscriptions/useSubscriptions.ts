// Subscriptions data hooks. Thin consumers of the canonical subscription
// endpoints. The DB is the source of truth; the backend resolves actual
// contracts and reconciles the live feed. The frontend is only the editor.
//
// No client-side expiry rollover, no client-side strike discovery, no direct
// broker calls — every mutation goes through the existing SubscriptionService.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addStock,
  applySubscriptions,
  deleteDerivativeRule,
  getSubscriptions,
  putDerivativeRule,
  removeStock,
  setIndexEnabled,
  setStockEnabled,
} from "@/api/market";
import type { DerivativeRule } from "@/types";

export function useSubscriptions() {
  return useQuery({
    queryKey: ["subscriptions"],
    queryFn: ({ signal }) => getSubscriptions(signal),
    refetchInterval: 15000,
  });
}

export interface SubscriptionMutations {
  toggleIndex: (label: string, enabled: boolean) => Promise<unknown>;
  addStock: (key: string, label: string) => Promise<unknown>;
  toggleStock: (key: string, enabled: boolean) => Promise<unknown>;
  removeStock: (key: string) => Promise<unknown>;
  saveRule: (rule: DerivativeRule) => Promise<unknown>;
  deleteRule: (underlying: string) => Promise<unknown>;
  apply: () => Promise<unknown>;
}

export function useSubscriptionMutations(): SubscriptionMutations {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["subscriptions"] });

  const indexMut = useMutation({
    mutationFn: ({ label, enabled }: { label: string; enabled: boolean }) =>
      setIndexEnabled(label, enabled),
    onSuccess: invalidate,
  });
  const addStockMut = useMutation({
    mutationFn: ({ key, label }: { key: string; label: string }) => addStock(key, label),
    onSuccess: invalidate,
  });
  const stockMut = useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) =>
      setStockEnabled(key, enabled),
    onSuccess: invalidate,
  });
  const removeStockMut = useMutation({
    mutationFn: (key: string) => removeStock(key),
    onSuccess: invalidate,
  });
  const ruleMut = useMutation({
    mutationFn: (rule: DerivativeRule) => putDerivativeRule(rule),
    onSuccess: invalidate,
  });
  const deleteRuleMut = useMutation({
    mutationFn: (underlying: string) => deleteDerivativeRule(underlying),
    onSuccess: invalidate,
  });
  const applyMut = useMutation({
    mutationFn: () => applySubscriptions(),
    onSuccess: invalidate,
  });

  return {
    toggleIndex: (label, enabled) => indexMut.mutateAsync({ label, enabled }),
    addStock: (key, label) => addStockMut.mutateAsync({ key, label }),
    toggleStock: (key, enabled) => stockMut.mutateAsync({ key, enabled }),
    removeStock: (key) => removeStockMut.mutateAsync(key),
    saveRule: (rule) => ruleMut.mutateAsync(rule),
    deleteRule: (underlying) => deleteRuleMut.mutateAsync(underlying),
    apply: () => applyMut.mutateAsync(),
  };
}
