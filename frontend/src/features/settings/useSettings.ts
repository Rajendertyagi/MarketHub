// Data hooks for the Settings feature. Thin consumers of the API module; all
// mutations invalidate the ["settings"] query group. React never computes data
// or performs auth/feed logic client-side. OAuth redirects are plain navigation
// functions (see api.ts), not mutations.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  backupDatabase,
  controlSource,
  createNewsSource,
  deleteNewsSource,
  deleteUpstoxCredentials,
  forgetFyersSession,
  forgetUpstoxSession,
  getAppSettings,
  getChatStatus,
  getFyersSettings,
  getNewsSources,
  getSourcesStatus,
  getUpstoxAuthStatus,
  getUpstoxCredStatus,
  getUpstoxFeedConfig,
  restartBrokerFeed,
  saveAppSettings,
  saveChatConfig,
  saveFyersCredentials,
  saveFyersFeedConfig,
  saveUpstoxCredentials,
  saveUpstoxFeedConfig,
  setNewsSourceEnabled,
  submitUpstoxToken,
  testNewsSource,
  updateNewsSource,
} from "./api";
import { SOURCES_REFRESH_MS } from "./constants";
import type {
  ChatConfigInput,
  FyersSettings,
  NewsSource,
  TestSourceInput,
  UpstoxFeedConfig,
} from "./types";

export function useAppSettings() {
  return useQuery({
    queryKey: ["settings", "app"],
    queryFn: ({ signal }) => getAppSettings(signal),
  });
}

export function useSaveAppSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (publicBaseUrl: string) => saveAppSettings(publicBaseUrl),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings", "app"] }),
  });
}

export function useUpstoxAuthStatus() {
  return useQuery({
    queryKey: ["settings", "upstox-auth"],
    queryFn: ({ signal }) => getUpstoxAuthStatus(signal),
    refetchInterval: SOURCES_REFRESH_MS,
  });
}

export function useUpstoxTokenLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (accessToken: string) => submitUpstoxToken(accessToken),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings", "upstox-auth"] }),
  });
}

export function useForgetUpstoxSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => forgetUpstoxSession(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings", "upstox-auth"] }),
  });
}

export function useUpstoxCredStatus() {
  return useQuery({
    queryKey: ["settings", "upstox-cred"],
    queryFn: ({ signal }) => getUpstoxCredStatus(signal),
  });
}

export function useSaveUpstoxCredentials() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { apiKey: string; apiSecret: string }) =>
      saveUpstoxCredentials(args.apiKey, args.apiSecret),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "upstox-auth"] });
      qc.invalidateQueries({ queryKey: ["settings", "upstox-cred"] });
    },
  });
}

export function useDeleteUpstoxCredentials() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => deleteUpstoxCredentials(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "upstox-auth"] });
      qc.invalidateQueries({ queryKey: ["settings", "upstox-cred"] });
    },
  });
}

export function useUpstoxFeedConfig() {
  return useQuery({
    queryKey: ["settings", "upstox-feed"],
    queryFn: ({ signal }) => getUpstoxFeedConfig(signal),
  });
}

export function useSaveUpstoxFeedConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => saveUpstoxFeedConfig(enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "upstox-feed"] });
      qc.invalidateQueries({ queryKey: ["settings", "sources"] });
    },
  });
}

export function useFyersSettings() {
  return useQuery({
    queryKey: ["settings", "fyers"],
    queryFn: ({ signal }) => getFyersSettings(signal),
    refetchInterval: SOURCES_REFRESH_MS,
  });
}

export function useSaveFyersCredentials() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { appId: string; secretId: string; pin?: string }) =>
      saveFyersCredentials(args.appId, args.secretId, args.pin),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings", "fyers"] }),
  });
}

export function useForgetFyersSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => forgetFyersSession(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings", "fyers"] }),
  });
}

export function useSaveFyersFeedConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => saveFyersFeedConfig(enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "fyers"] });
      qc.invalidateQueries({ queryKey: ["settings", "sources"] });
    },
  });
}

export function useNewsSources() {
  return useQuery({
    queryKey: ["settings", "news-sources"],
    queryFn: ({ signal }) => getNewsSources(signal),
  });
}

export interface NewsSourceMutations {
  create: (source: NewsSource) => Promise<unknown>;
  update: (args: { id: string; source: NewsSource }) => Promise<unknown>;
  remove: (id: string) => Promise<unknown>;
  setEnabled: (args: { id: string; enabled: boolean }) => Promise<unknown>;
  test: (input: TestSourceInput) => Promise<unknown>;
}

export function useNewsSourceMutations(): NewsSourceMutations {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["settings", "news-sources"] });

  const createMut = useMutation({
    mutationFn: (source: NewsSource) => createNewsSource(source),
    onSuccess: invalidate,
  });
  const updateMut = useMutation({
    mutationFn: ({ id, source }: { id: string; source: NewsSource }) =>
      updateNewsSource(id, source),
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
    mutationFn: (input: TestSourceInput) => testNewsSource(input),
  });

  return {
    create: (source) => createMut.mutateAsync(source),
    update: ({ id, source }) => updateMut.mutateAsync({ id, source }),
    remove: (id) => deleteMut.mutateAsync(id),
    setEnabled: ({ id, enabled }) => enableMut.mutateAsync({ id, enabled }),
    test: (input) => testMut.mutateAsync(input),
  };
}

export function useSourcesStatus() {
  return useQuery({
    queryKey: ["settings", "sources"],
    queryFn: ({ signal }) => getSourcesStatus(signal),
    refetchInterval: SOURCES_REFRESH_MS,
  });
}

export function useSourceControl() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["settings", "sources"] });

  const controlMut = useMutation({
    mutationFn: (args: { name: string; action: "start" | "stop" | "restart" }) =>
      controlSource(args.name, args.action),
    onSuccess: invalidate,
  });
  const restartMut = useMutation({
    mutationFn: (name: string) => restartBrokerFeed(name),
    onSuccess: invalidate,
  });

  return {
    control: (name: string, action: "start" | "stop" | "restart") =>
      controlMut.mutateAsync({ name, action }),
    restart: (name: string) => restartMut.mutateAsync(name),
  };
}

export function useChatStatus() {
  return useQuery({
    queryKey: ["settings", "chat"],
    queryFn: ({ signal }) => getChatStatus(signal),
  });
}

export function useSaveChatConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (config: ChatConfigInput) => saveChatConfig(config),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings", "chat"] }),
  });
}

export function useBackup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => backupDatabase(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["settings", "backup"] }),
  });
}

export type { FyersSettings, UpstoxFeedConfig };
