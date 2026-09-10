// Data hooks for the News / Sentiment feature. Thin consumers of the API
// module; all mutations invalidate the ["news"] query group. React never
// derives sentiment or aggregates articles client-side.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createNewsSource,
  deleteNewsSource,
  getNews,
  getNewsSentiment,
  getNewsSources,
  refreshNews,
  setNewsSourceEnabled,
  testNewsSource,
  updateNewsSource,
} from "./api";
import type { NewsFilters, NewsSourceInput, NewsSourceType } from "./types";

export function useNews(filters: NewsFilters) {
  return useQuery({
    queryKey: ["news", "list", filters],
    queryFn: ({ signal }) => getNews(filters, signal),
    refetchInterval: 30000,
  });
}

export function useNewsSentiment(filters: NewsFilters) {
  return useQuery({
    queryKey: ["news", "sentiment", filters],
    queryFn: ({ signal }) => getNewsSentiment(filters, signal),
    refetchInterval: 30000,
  });
}

export function useNewsSources() {
  return useQuery({
    queryKey: ["news", "sources"],
    queryFn: ({ signal }) => getNewsSources(signal),
    refetchInterval: 30000,
  });
}

export interface NewsMutations {
  refresh: (opts?: { source_ids?: string[]; limit_per_source?: number }) => Promise<unknown>;
  createSource: (input: NewsSourceInput) => Promise<unknown>;
  updateSource: (args: { id: string; input: Partial<NewsSourceInput> }) => Promise<unknown>;
  deleteSource: (id: string) => Promise<unknown>;
  setSourceEnabled: (args: { id: string; enabled: boolean }) => Promise<unknown>;
  testSource: (
    args: { type: NewsSourceType; config: Record<string, unknown> },
  ) => Promise<unknown>;
}

export function useNewsMutations(): NewsMutations {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["news"] });

  const refreshMut = useMutation({
    mutationFn: (
      opts?: { source_ids?: string[]; limit_per_source?: number },
    ) => refreshNews(opts),
  });
  const createMut = useMutation({
    mutationFn: (input: NewsSourceInput) => createNewsSource(input),
    onSuccess: invalidate,
  });
  const updateMut = useMutation({
    mutationFn: ({ id, input }: { id: string; input: Partial<NewsSourceInput> }) =>
      updateNewsSource(id, input),
    onSuccess: invalidate,
  });
  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteNewsSource(id),
    onSuccess: invalidate,
  });
  const enableMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      setNewsSourceEnabled(id, enabled),
    onSuccess: invalidate,
  });
  const testMut = useMutation({
    mutationFn: ({
      type,
      config,
    }: {
      type: NewsSourceType;
      config: Record<string, unknown>;
    }) => testNewsSource(type, config),
  });

  return {
    refresh: (opts) => refreshMut.mutateAsync(opts),
    createSource: (input) => createMut.mutateAsync(input),
    updateSource: ({ id, input }) => updateMut.mutateAsync({ id, input }),
    deleteSource: (id) => deleteMut.mutateAsync(id),
    setSourceEnabled: ({ id, enabled }) =>
      enableMut.mutateAsync({ id, enabled }),
    testSource: ({ type, config }) => testMut.mutateAsync({ type, config }),
  };
}
