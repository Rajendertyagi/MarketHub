// Depth fetch for the quote drawer. On-demand GET only — no continuous polling.
// React renders the depth levels; it never aggregates or derives them.
import { useQuery } from "@tanstack/react-query";
import { getQuoteDepth } from "./api";

export function useQuoteDepth(
  exchange: string | null,
  instrumentToken: string | null,
) {
  return useQuery({
    queryKey: ["market", "depth", exchange, instrumentToken],
    queryFn: ({ signal }) =>
      getQuoteDepth(exchange as string, instrumentToken as string, signal),
    enabled: Boolean(exchange && instrumentToken),
    refetchInterval: 5000,
  });
}
