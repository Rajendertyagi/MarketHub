// Unified Market Data page hooks. Thin consumers of the F&O coverage API.
// Catalog status comes from the existing instruments hooks; live coverage,
// preview and apply/release come from the F&O coverage endpoints.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  applyFnoCoverage,
  getFnoCoverage,
  previewFnoCoverage,
  releaseFnoCoverage,
} from "@/api/market";

export function useFnoCoverage(expiry?: string) {
  return useQuery({
    queryKey: ["fno-coverage", expiry ?? ""],
    queryFn: ({ signal }) => getFnoCoverage(expiry, signal),
    refetchInterval: 15000,
  });
}

export function useFnoMutations() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["fno-coverage"] });
  const applyMut = useMutation({
    mutationFn: (expiry?: string) => applyFnoCoverage(expiry),
    onSuccess: invalidate,
  });
  const releaseMut = useMutation({
    mutationFn: () => releaseFnoCoverage(),
    onSuccess: invalidate,
  });
  const previewMut = useMutation({
    mutationFn: (expiry?: string) => previewFnoCoverage(expiry),
  });
  return {
    apply: (expiry?: string) => applyMut.mutateAsync(expiry),
    release: () => releaseMut.mutateAsync(),
    preview: (expiry?: string) => previewMut.mutateAsync(expiry),
  };
}
