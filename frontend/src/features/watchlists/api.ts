// Thin API layer for Watchlists. Every call goes through the shared
// `request()` helper — no hardcoded URLs, no direct fetch.
import { request } from "@/api/client";
import { watchlistsResponseSchema } from "./schemas";
import type { WatchlistsResponse } from "./types";

const WATCHLISTS = "/watchlists";

export async function getWatchlists(
  signal?: AbortSignal,
): Promise<WatchlistsResponse> {
  return request<WatchlistsResponse>(WATCHLISTS, {
    schema: watchlistsResponseSchema,
    signal,
  });
}

export async function createWatchlist(name: string, signal?: AbortSignal) {
  return request(WATCHLISTS, {
    method: "POST",
    body: { name },
    signal,
  });
}

export async function renameWatchlist(
  id: number,
  name: string,
  signal?: AbortSignal,
) {
  return request(`${WATCHLISTS}/${id}`, {
    method: "PATCH",
    body: { name },
    signal,
  });
}

export async function deleteWatchlist(id: number, signal?: AbortSignal) {
  return request(`${WATCHLISTS}/${id}`, { method: "DELETE", signal });
}

export interface AddWatchlistItemInput {
  exchange: string;
  instrument_token: string;
  tradingsymbol: string;
}

export async function addWatchlistItem(
  watchlistId: number,
  item: AddWatchlistItemInput,
  signal?: AbortSignal,
) {
  return request(`${WATCHLISTS}/${watchlistId}/items`, {
    method: "POST",
    body: item,
    signal,
  });
}

export async function removeWatchlistItem(itemId: number, signal?: AbortSignal) {
  return request(`${WATCHLISTS}/items/${itemId}`, {
    method: "DELETE",
    signal,
  });
}

export async function exportWatchlists(): Promise<Blob> {
  const resp = await fetch("/api/watchlists/export");
  if (!resp.ok) throw new Error("Export failed");
  return resp.blob();
}

export async function importWatchlists(file: File) {
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch("/api/watchlists/import", { method: "POST", body: form });
  if (!resp.ok) throw new Error("Import failed");
  return resp.json();
}
