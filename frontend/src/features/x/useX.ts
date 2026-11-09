// Data hooks for the X/Twitter feature. Thin consumers of the API module;
// all mutations invalidate the ["x"] query group. React never derives
// auth/feed semantics client-side.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  deleteXCredentials,
  getXCredStatus,
  getXPollConfig,
  getXStatus,
  getXTweets,
  saveXCredentials,
  saveXPollConfig,
  testXConnection,
} from "./api";
import { X_STATUS_REFRESH_MS } from "./constants";
import type { XPollConfigInput } from "./types";

export function useXCredStatus() {
  return useQuery({
    queryKey: ["x", "cred"],
    queryFn: ({ signal }) => getXCredStatus(signal),
    refetchInterval: X_STATUS_REFRESH_MS,
  });
}

export function useXPollConfig() {
  return useQuery({
    queryKey: ["x", "config"],
    queryFn: ({ signal }) => getXPollConfig(signal),
  });
}

export function useXStatus() {
  return useQuery({
    queryKey: ["x", "status"],
    queryFn: ({ signal }) => getXStatus(signal),
    refetchInterval: X_STATUS_REFRESH_MS,
  });
}

export function useXTweets(limit = 20) {
  return useQuery({
    queryKey: ["x", "tweets", limit],
    queryFn: ({ signal }) => getXTweets({ limit }, signal),
    refetchInterval: X_STATUS_REFRESH_MS,
  });
}

export function useXMutations() {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["x"] });

  const saveCreds = useMutation({
    mutationFn: (args: { authToken: string; ct0: string }) =>
      saveXCredentials(args.authToken, args.ct0),
    onSuccess: invalidate,
  });
  const deleteCreds = useMutation({
    mutationFn: () => deleteXCredentials(),
    onSuccess: invalidate,
  });
  const saveConfig = useMutation({
    mutationFn: (input: XPollConfigInput) => saveXPollConfig(input),
    onSuccess: invalidate,
  });
  const testConn = useMutation({
    mutationFn: () => testXConnection(),
    onSuccess: invalidate,
  });

  return {
    saveCreds: (authToken: string, ct0: string) => saveCreds.mutateAsync({ authToken, ct0 }),
    deleteCreds: () => deleteCreds.mutateAsync(),
    saveConfig: (input: XPollConfigInput) => saveXPollConfig(input),
    testConnection: () => testConn.mutateAsync(),
    saving: saveCreds.isPending || deleteCreds.isPending || saveConfig.isPending,
    testing: testConn.isPending,
  };
}
