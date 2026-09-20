// Instruments / catalog data hooks. Thin consumers of the canonical instrument
// endpoints. React reads catalog/source status and edits segment preferences;
// the backend performs the master sync and owns segment semantics (including
// provider-specific behavior such as Fyers NSE_EQ vs NSE_INDEX). The frontend
// never parses provider master files or infers instrument type client-side.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getSegments, getSyncState, setSegments, syncInstruments } from "@/api/market";

export function useSegments() {
  return useQuery({
    queryKey: ["instruments", "segments"],
    queryFn: ({ signal }) => getSegments(signal),
    refetchInterval: 30000,
  });
}

export function useSyncState() {
  return useQuery({
    queryKey: ["instruments", "sync-state"],
    queryFn: ({ signal }) => getSyncState(signal),
    refetchInterval: 30000,
  });
}

export function useInstrumentMutations() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["instruments"] });

  const segmentsMut = useMutation({
    mutationFn: (segments: string[]) => setSegments(segments),
    onSuccess: invalidate,
  });
  const syncMut = useMutation({
    mutationFn: (provider: string) => syncInstruments(provider),
    onSuccess: invalidate,
  });

  return {
    saveSegments: (segments: string[]) => segmentsMut.mutateAsync(segments),
    sync: (provider: string) => syncMut.mutateAsync(provider),
  };
}
