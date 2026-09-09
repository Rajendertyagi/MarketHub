// F&O Workspace + Option Chain data hooks.
//
// All hooks are thin consumers of the existing canonical endpoints. They own no
// canonical logic: IV stays a fraction, ATM is backend-resolved, greeks/quotes
// pass through. The active-view subscription reuses the existing bounded owner
// (POST /api/market/fno/view) — no second subscription owner is introduced.

import { useMemo } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type {
  ChainLegView,
  ChainRow,
  ChainRowView,
  FnoFuture,
  FnoOption,
  FnoUnderlyingKind,
  Quote,
} from "@/types";
import {
  getFnoUniverse,
  getFnoWorkspace,
  getFutures,
  getOptionChainView,
  postFnoView,
  type FnoViewParams,
} from "@/api/market";

export interface CombinedUnderlying {
  symbol: string;
  name: string | null;
  kind: FnoUnderlyingKind;
  futures_available?: boolean;
  options_available?: boolean;
}

// Curated index underlyings for the Option Chain (legacy OC_ALLOW). The
// /api/options/underlyings endpoint returns raw derivative tokens, not these
// canonical index names; the Option Chain UI is scoped to these four indices,
// each of which is supported by /api/options/chain/view.
export const INDEX_UNDERLYINGS = [
  "NIFTY",
  "BANKNIFTY",
  "FINNIFTY",
  "MIDCPNIFTY",
] as const;

export function useFnoUnderlyings() {
  const equity = useQuery({
    queryKey: ["fno-universe"],
    queryFn: ({ signal }) => getFnoUniverse("", 500, signal),
  });

  const all = useMemo<CombinedUnderlying[]>(() => {
    const eq = (equity.data?.universe ?? []).map((r) => ({
      symbol: r.symbol,
      name: r.name,
      kind: "equity" as const,
      futures_available: r.futures_available,
      options_available: r.options_available,
    }));
    const idx = INDEX_UNDERLYINGS.map((s) => ({
      symbol: s,
      name: null,
      kind: "index" as const,
    }));
    return [...eq, ...idx].sort((a, b) => a.symbol.localeCompare(b.symbol));
  }, [equity.data]);

  return {
    equity,
    all,
    isLoading: equity.isLoading,
    error: equity.error,
  };
}

export function useEquityWorkspace(symbol: string, windowSize: number) {
  return useQuery({
    queryKey: ["fno-workspace", symbol, windowSize],
    enabled: !!symbol,
    queryFn: ({ signal }) =>
      getFnoWorkspace({ symbol, window: windowSize }, signal),
    refetchInterval: 5000,
  });
}

export function useIndexChain(
  underlying: string,
  expiry: string,
  windowSize: number,
  enabled = true,
) {
  return useQuery({
    queryKey: ["option-chain", underlying, expiry, windowSize],
    enabled: !!underlying && enabled,
    queryFn: ({ signal }) =>
      getOptionChainView({ underlying, expiry, window: windowSize }, signal),
    refetchInterval: 5000,
  });
}

export function useFutures(underlying: string, kind: FnoUnderlyingKind | "") {
  return useQuery({
    queryKey: ["futures", underlying, kind],
    enabled: kind === "index" && !!underlying,
    queryFn: ({ signal }) => getFutures(underlying, undefined, signal),
    refetchInterval: 5000,
  });
}

// Best-effort bounded active-view subscription (equity F&O only). The view still
// renders canonical quotes even if coverage cannot be applied; failures are
// swallowed so a missing feed never blanks the workspace.
export function useFnoActiveView() {
  return useMutation({
    mutationFn: (params: FnoViewParams) => postFnoView(params),
  });
}

// ── Presentation normalizers (pure; no canonical recomputation) ─────────────────

export function bidOf(q?: Quote | null): number | null | undefined {
  if (!q) return undefined;
  return q.bid ?? q.best_bid;
}

export function askOf(q?: Quote | null): number | null | undefined {
  if (!q) return undefined;
  return q.ask ?? q.best_ask;
}

export function normalizeEquityOptions(
  options: FnoOption[],
  atm: number | null,
): ChainRowView[] {
  const byStrike = new Map<number, ChainRowView>();
  for (const o of options) {
    const row =
      byStrike.get(o.strike) ??
      ({ strike: o.strike, atm: false } as ChainRowView);
    const leg: ChainLegView = {
      key: o.key,
      label: o.label,
      exchange: "",
      option_type: o.option_type,
      strike: o.strike,
      quote: o.quote,
    };
    if (o.option_type === "CE") row.call = leg;
    else row.put = leg;
    byStrike.set(o.strike, row);
  }
  const rows = [...byStrike.values()];
  if (atm != null) {
    for (const r of rows) r.atm = r.strike === atm;
  }
  rows.sort((a, b) => a.strike - b.strike);
  return rows;
}

export function normalizeChainRows(rows: ChainRow[]): ChainRowView[] {
  return rows
    .map((r) => ({
      strike: r.strike,
      atm: r.atm,
      call: r.call
        ? {
            key: r.call.instrument_key,
            label: r.call.symbol,
            exchange: r.call.exchange,
            option_type: r.call.option_type,
            strike: r.call.strike,
            quote: r.call.quote,
          }
        : undefined,
      put: r.put
        ? {
            key: r.put.instrument_key,
            label: r.put.symbol,
            exchange: r.put.exchange,
            option_type: r.put.option_type,
            strike: r.put.strike,
            quote: r.put.quote,
          }
        : undefined,
    }))
    .sort((a, b) => a.strike - b.strike);
}

export type { FnoFuture };
