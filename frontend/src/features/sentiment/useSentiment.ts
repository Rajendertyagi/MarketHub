// Data hooks for the Sentiment dashboard. Reuses the News feature's API surface
// (same backend endpoint) so there is a single source of truth for news data.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getNewsSentiment, refreshNews } from "@/features/news/api";
import { useNewsSources } from "@/features/news/useNews";
import type { NewsFilters } from "@/features/news/types";

export function useSentiment(filters: NewsFilters) {
  return useQuery({
    queryKey: ["sentiment", "view", filters],
    queryFn: ({ signal }) => getNewsSentiment(filters, signal),
    refetchInterval: 30_000,
  });
}

export function useSentimentSources() {
  return useNewsSources();
}

export function useSentimentRefresh() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => refreshNews(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sentiment"] }),
  });
}
