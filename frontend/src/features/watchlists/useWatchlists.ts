// Data hooks for Watchlists. Thin consumers of the API module; all mutations
// invalidate the ["watchlists"] query group.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addWatchlistItem,
  createWatchlist,
  deleteWatchlist,
  getWatchlists,
  removeWatchlistItem,
  renameWatchlist,
} from "./api";
import { WATCHLISTS_REFRESH_MS } from "./constants";
import type { AddWatchlistItemInput } from "./api";

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
  addItem: (
    args: { watchlistId: number } & AddWatchlistItemInput,
  ) => Promise<unknown>;
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

  const addItemMut = useMutation({
    mutationFn: ({ watchlistId, ...item }: { watchlistId: number } & AddWatchlistItemInput) =>
      addWatchlistItem(watchlistId, item),
    onSuccess: invalidate,
  });

  return {
    addItem: (args) => addItemMut.mutateAsync(args),
    create: (name) => createMut.mutateAsync(name),
    rename: ({ id, name }) => renameMut.mutateAsync({ id, name }),
    remove: (id) => deleteMut.mutateAsync(id),
    removeItem: (itemId) => removeItemMut.mutateAsync(itemId),
  };
}
