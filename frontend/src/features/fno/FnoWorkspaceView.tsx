import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ApiError,
  type ChainRowView,
  type FutureView,
  type FnoUnderlyingKind,
} from "@/types";
import { AsyncStateView, Field, Select, Tabs } from "@/components/ui";
import {
  normalizeChainRows,
  normalizeEquityOptions,
  useEquityWorkspace,
  useFnoActiveView,
  useFnoUnderlyings,
  useFutures,
  useIndexChain,
} from "./useFno";
import { UnderlyingPicker } from "./UnderlyingPicker";
import { SpotHeader } from "./SpotHeader";
import { FuturesTable } from "./FuturesTable";
import { OptionChainTable } from "./OptionChainTable";
import { OptionAnalytics } from "./OptionAnalytics";

type Tab = "chain" | "futures";

const WINDOWS = [5, 10, 15, 20, 25];

// Major indices surfaced at the top of the symbol dropdown so the user can jump
// straight to NIFTY/BANKNIFTY/etc. Order here defines dropdown order.
const MAJOR_INDICES = [
  "NIFTY",
  "BANKNIFTY",
  "FINNIFTY",
  "MIDCPNIFTY",
];

export function FnoWorkspaceView() {
  const [params, setParams] = useSearchParams();
  const sym = params.get("sym") ?? "";
  const kind = (params.get("kind") as "equity" | "index" | null) ?? null;
  const expiry = params.get("expiry") ?? "";
  const windowSize = Number(params.get("window") ?? "10") || 10;
  const tab = (params.get("tab") as Tab) ?? "chain";
  const [showGreeks, setShowGreeks] = useState(true);

  const { all, isLoading: uLoading } = useFnoUnderlyings();
  const activeView = useFnoActiveView();

  const equity = useEquityWorkspace(sym, windowSize);
  const indexChain = useIndexChain(sym, expiry, windowSize, kind === "index");
  const futures = useFutures(sym, kind ?? "");

  // Bootstrap index expiry from the loaded chain (backend defaults to first).
  useEffect(() => {
    if (kind === "index" && !expiry && indexChain.data) {
      const first =
        indexChain.data.expiry || indexChain.data.expiries_available[0];
      if (first) {
        setParams(
          (prev) => {
            const n = new URLSearchParams(prev);
            n.set("expiry", first);
            return n;
          },
          { replace: true },
        );
      }
    }
  }, [kind, expiry, indexChain.data, setParams]);

  // Equity F&O: establish the bounded active-view subscription (reuses the
  // existing owner). Best-effort; never blocks rendering.
  useEffect(() => {
    if (kind === "equity" && sym) {
      activeView.mutate({ symbol: sym, window: windowSize });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, sym, windowSize]);

  const setParam = (key: string, value: string) => {
    setParams(
      (prev) => {
        const n = new URLSearchParams(prev);
        n.set(key, value);
        return n;
      },
      { replace: true },
    );
  };

  // Symbol → kind lookup so the on-page dropdown can switch underlyings directly.
  const bySymbol = useMemo(() => {
    const m = new Map<string, FnoUnderlyingKind>();
    for (const u of all) m.set(u.symbol, u.kind);
    return m;
  }, [all]);

  // Group the dropdown: major indices first (pinned, in fixed order), then any
  // other indices, then equities — so the user can grab NIFTY/BANKNIFTY fast.
  const groups = useMemo(() => {
    const major: typeof all = [];
    const otherIdx: typeof all = [];
    const eq: typeof all = [];
    const majorOrder = new Map(MAJOR_INDICES.map((s, i) => [s, i]));
    for (const u of all) {
      if (u.kind === "index" && majorOrder.has(u.symbol)) major.push(u);
      else if (u.kind === "index") otherIdx.push(u);
      else eq.push(u);
    }
    major.sort(
      (a, b) => (majorOrder.get(a.symbol) ?? 0) - (majorOrder.get(b.symbol) ?? 0),
    );
    otherIdx.sort((a, b) => a.symbol.localeCompare(b.symbol));
    eq.sort((a, b) => a.symbol.localeCompare(b.symbol));
    return { major, otherIdx, eq };
  }, [all]);

  const selectUnderlying = (symbol: string, k: "equity" | "index") => {
    setParams(
      (prev) => {
        const n = new URLSearchParams(prev);
        n.set("sym", symbol);
        n.set("kind", k);
        n.delete("expiry");
        n.set("tab", "chain");
        return n;
      },
      { replace: true },
    );
  };

  const onSymbolChange = (symbol: string) => {
    selectUnderlying(symbol, bySymbol.get(symbol) ?? "index");
  };

  const back = () => {
    setParams(
      (prev) => {
        const n = new URLSearchParams(prev);
        n.delete("sym");
        n.delete("kind");
        n.delete("expiry");
        n.delete("tab");
        return n;
      },
      { replace: true },
    );
  };

  const isEquity = kind === "equity";
  const ws = equity.data;
  const chain = indexChain.data;

  // Normalize the two backend shapes into one ChainRowView[] model.
  const rows: ChainRowView[] = useMemo(() => {
    if (isEquity && ws) return normalizeEquityOptions(ws.options, ws.atm);
    if (!isEquity && chain) return normalizeChainRows(chain.rows);
    return [];
  }, [isEquity, ws, chain]);

  const futuresView: FutureView[] = useMemo(() => {
    if (isEquity && ws) {
      return ws.futures.map((f) => ({
        key: f.key,
        label: f.label,
        exchange: "",
        expiry: f.expiry,
        quote: f.quote,
      }));
    }
    if (!isEquity && futures.data) {
      return (futures.data.contracts ?? []).map((c) => ({
        key: c.instrument_key,
        label: c.symbol,
        exchange: c.exchange,
        expiry: c.expiry ?? "",
        quote: c.ltp != null ? { ltp: c.ltp } : null,
      }));
    }
    return [];
  }, [isEquity, ws, futures.data]);

  // Header values (unified across both shapes).
  const header = isEquity
    ? {
        ltp: ws?.spot_quote?.ltp ?? null,
        change: ws?.spot_quote?.change ?? null,
        changePercent: ws?.spot_quote?.change_percent ?? null,
        atm: ws?.atm ?? null,
        spotBasis: ws?.atm_basis ?? null,
        notes: ws?.notes ?? [],
      }
    : {
        ltp: chain?.spot ?? null,
        change: null,
        changePercent: null,
        atm: chain?.atm_strike ?? null,
        spotBasis: chain?.spot_basis ?? null,
        notes: [],
      };

  const loading = isEquity ? equity.isLoading : indexChain.isLoading;
  const error = isEquity ? equity.error : indexChain.error;

  const tabs = [
    { value: "chain" as Tab, label: "Option Chain" },
    { value: "futures" as Tab, label: "Futures" },
  ];

  let body: React.ReactNode;
  if (loading) {
    body = <AsyncStateView status="loading" loadingLabel="Loading workspace…" />;
  } else if (error) {
    body = (
      <AsyncStateView
        status="error"
        error={error as ApiError}
        onRetry={() => (isEquity ? equity.refetch() : indexChain.refetch())}
      />
    );
  } else if (!rows.length && !futuresView.length && tab !== "chain") {
    body = (
      <AsyncStateView
        status="empty"
        emptyLabel="No contracts available for this selection."
      />
    );
  } else {
     switch (tab) {
       case "chain":
         body = (
           <>
             <OptionChainTable
               rows={rows}
               spot={header.ltp}
               showGreeks={showGreeks}
             />
              {!isEquity && chain ? (
                <section className="fno-analytics-panel">
                  <h2 className="fno-analytics-heading">Chain Analytics</h2>
                  <OptionAnalytics analytics={chain.analytics} rows={rows} />
                </section>
              ) : null}
           </>
         );
         break;
       case "futures":
         body = <FuturesTable futures={futuresView} />;
         break;
     }
  }

  const expiryOptions = isEquity
    ? ws?.option_expiries ?? []
    : chain?.expiries_available ?? [];

  if (!sym || !kind) {
    return (
      <UnderlyingPicker
        underlyings={all}
        isLoading={uLoading}
        onSelect={selectUnderlying}
      />
    );
  }

  return (
    <div className="panel fno-workspace">
      <SpotHeader
        symbol={sym}
        kind={kind}
        ltp={header.ltp}
        change={header.change}
        changePercent={header.changePercent}
        atm={header.atm}
        spotBasis={header.spotBasis}
        notes={header.notes}
        onBack={back}
      />

      <div className="toolbar fno-controls">
        <Field label="Symbol">
          <Select value={sym} onChange={(e) => onSymbolChange(e.target.value)}>
            {groups.major.length > 0 && (
              <optgroup label="Major Indices">
                {groups.major.map((u) => (
                  <option key={`${u.kind}:${u.symbol}`} value={u.symbol}>
                    {u.symbol}
                    {u.name ? ` · ${u.name}` : ""}
                  </option>
                ))}
              </optgroup>
            )}
            {groups.otherIdx.length > 0 && (
              <optgroup label="Indices">
                {groups.otherIdx.map((u) => (
                  <option key={`${u.kind}:${u.symbol}`} value={u.symbol}>
                    {u.symbol}
                    {u.name ? ` · ${u.name}` : ""}
                  </option>
                ))}
              </optgroup>
            )}
            {groups.eq.length > 0 && (
              <optgroup label="Equity">
                {groups.eq.map((u) => (
                  <option key={`${u.kind}:${u.symbol}`} value={u.symbol}>
                    {u.symbol}
                    {u.name ? ` · ${u.name}` : ""}
                  </option>
                ))}
              </optgroup>
            )}
          </Select>
        </Field>
        <Field label="Expiry">
          <Select
            value={isEquity ? ws?.selected_expiry ?? "" : expiry}
            onChange={(e) => setParam("expiry", e.target.value)}
            disabled={!expiryOptions.length}
          >
            {expiryOptions.length === 0 && <option value="">—</option>}
            {expiryOptions.map((e) => (
              <option key={e} value={e}>
                {e}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Window">
          <Select
            value={String(windowSize)}
            onChange={(e) => setParam("window", e.target.value)}
          >
            {WINDOWS.map((w) => (
              <option key={w} value={w}>
                ±{w}
              </option>
            ))}
          </Select>
        </Field>
        <label className="check">
          <input
            type="checkbox"
            checked={showGreeks}
            onChange={(e) => setShowGreeks(e.target.checked)}
          />
          Greeks
        </label>
        {activeView.isPending && isEquity && (
          <span className="hint">applying live view…</span>
        )}
      </div>

      <Tabs tabs={tabs} active={tab} onChange={(t) => setParam("tab", t)} />

      <div className="fno-tab-body">{body}</div>
    </div>
  );
}
