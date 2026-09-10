// Data hooks for Watchlists. Thin consumers of the API module; all mutations
// invalidate the ["watchlists"] query group.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createWatchlist,
  deleteWatchlist,
  getWatchlists,
  removeWatchlistItem,
  renameWatchlist,
} from "./api";
import { WATCHLISTS_REFRESH_MS } from "./constants";

export function useWatchlists() {
  return useQuery({
    queryKey: ["watchlists", "list"],
    queryFn: ({ signal }) => getWatchlists(signal),
    refetchInterval: WATCHLISTS_REFRESH_MS,
  });
}

export interface WatchlistMutations {
  create: (name: string) => Promise<unknown>;
  rename: (args: { id: number; name: string }) => Promise<unknown>;
  remove: (id: number) => Promise<unknown>;
  removeItem: (itemId: number) => Promise<unknown>;
}

export function useWatchlistMutations(): WatchlistMutations {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["watchlists"] });

  const createMut = useMutation({
    mutationFn: (name: string) => createWatchlist(name),
    onSuccess: invalidate,
  });
  const renameMut = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) =>
      renameWatchlist(id, name),
    onSuccess: invalidate,
  });
  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteWatchlist(id),
    onSuccess: invalidate,
  });
  const removeItemMut = useMutation({
    mutationFn: (itemId: number) => removeWatchlistItem(itemId),
    onSuccess: invalidate,
  });

  return {
    create: (name) => createMut.mutateAsync(name),
    rename: ({ id, name }) => renameMut.mutateAsync({ id, name }),
    remove: (id) => deleteMut.mutateAsync(id),
    removeItem: (itemId) => removeItemMut.mutateAsync(itemId),
  };
}
