import { useEffect, useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { ApiError, type ChainRowView, type FutureView } from "@/types";
import {
  AsyncStateView,
  Field,
  Select,
  Tabs,
} from "@/components/ui";
import { fmtInt, fmtNum } from "@/utils/format";
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

type Tab = "overview" | "futures" | "chain" | "greeks" | "analytics";

const WINDOWS = [5, 10, 15, 20, 25];

export function FnoWorkspaceView() {
  const [params, setParams] = useSearchParams();
  const sym = params.get("sym") ?? "";
  const kind = (params.get("kind") as "equity" | "index" | null) ?? null;
  const expiry = params.get("expiry") ?? "";
  const windowSize = Number(params.get("window") ?? "10") || 10;
  const tab = (params.get("tab") as Tab) ?? "overview";

  const { all, isLoading: uLoading } = useFnoUnderlyings();
  const activeView = useFnoActiveView();

  const equity = useEquityWorkspace(sym, windowSize);
  const indexChain = useIndexChain(sym, expiry, windowSize, kind === "index");
  const futures = useFutures(sym, kind ?? "");

  // Bootstrap index expiry from the loaded chain (backend defaults to first).
  useEffect(() => {
    if (kind === "index" && !expiry && indexChain.data) {
      const first = indexChain.data.expiry || indexChain.data.expiries_available[0];
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

  const selectUnderlying = (symbol: string, k: "equity" | "index") => {
    setParams(
      (prev) => {
        const n = new URLSearchParams(prev);
        n.set("sym", symbol);
        n.set("kind", k);
        n.delete("expiry");
        n.set("tab", "overview");
        return n;
      },
      { replace: true },
    );
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
    { value: "overview" as Tab, label: "Overview" },
    { value: "futures" as Tab, label: "Futures" },
    { value: "chain" as Tab, label: "Option Chain" },
    { value: "greeks" as Tab, label: "Greeks" },
    ...(isEquity ? [] : [{ value: "analytics" as Tab, label: "Analytics" }]),
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
  } else if (!rows.length && !futuresView.length && tab !== "overview") {
    body = (
      <AsyncStateView
        status="empty"
        emptyLabel="No contracts available for this selection."
      />
    );
  } else {
    switch (tab) {
      case "overview":
        body = <OverviewTab header={header} futuresCount={futuresView.length} rows={rows} />;
        break;
      case "futures":
        body = <FuturesTable futures={futuresView} />;
        break;
      case "chain":
        body = (
          <>
            <OptionChainTable rows={rows} />
            {!isEquity && chain && (
              <OptionAnalytics analytics={chain.analytics} rows={rows} />
            )}
          </>
        );
        break;
      case "greeks":
        body = <GreeksLadder rows={rows} />;
        break;
      case "analytics":
        body =
          !isEquity && chain ? (
            <OptionAnalytics analytics={chain.analytics} rows={rows} />
          ) : null;
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
    <div className="panel">
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

      <div className="control-row">
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
        {activeView.isPending && isEquity && (
          <span className="hint">applying live view…</span>
        )}
      </div>

      <Tabs
        tabs={tabs}
        active={tab}
        onChange={(t) => setParam("tab", t)}
      />

      <div className="fno-tab-body">{body}</div>
    </div>
  );
}

function OverviewTab({
  header,
  futuresCount,
  rows,
}: {
  header: { atm: number | null; notes: string[] };
  futuresCount: number;
  rows: ChainRowView[];
}) {
  return (
    <div className="fno-analytics">
      <div className="stat-grid">
        <div className="stat-card">
          <span className="stat-label">ATM Strike</span>
          <span className="stat-value">
            {header.atm == null ? "—" : fmtNum(header.atm)}
          </span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Futures</span>
          <span className="stat-value">{fmtInt(futuresCount)}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Option Strikes</span>
          <span className="stat-value">{fmtInt(rows.length)}</span>
        </div>
      </div>
      {header.notes.length > 0 && (
        <div className="card">
          <div className="hint">{header.notes.join("; ")}</div>
        </div>
      )}
    </div>
  );
}

// Dedicated Greeks ladder (canonical values passed through; null → "—").
function GreeksLadder({ rows }: { rows: ChainRowView[] }) {
  if (!rows.length) {
    return (
      <div className="state">
        <span className="muted">No option contracts for this selection.</span>
      </div>
    );
  }
  const cell = (v: number | null | undefined, d = 2) => (
    <td className="num">{v == null ? "—" : fmtNum(v, d)}</td>
  );
  return (
    <div className="card" style={{ padding: 0, overflow: "auto" }}>
      <table className="table">
        <thead>
          <tr>
            <th>Strike</th>
            <th className="num">CE Δ</th>
            <th className="num">CE Γ</th>
            <th className="num">CE Θ</th>
            <th className="num">CE Vega</th>
            <th className="num">CE ρ</th>
            <th className="num">PE Δ</th>
            <th className="num">PE Γ</th>
            <th className="num">PE Θ</th>
            <th className="num">PE Vega</th>
            <th className="num">PE ρ</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.strike} className={r.atm ? "option-chain-atm" : ""}>
              <td>
                <b>{fmtNum(r.strike)}</b>
              </td>
              {cell(r.call?.quote?.delta)}
              {cell(r.call?.quote?.gamma, 3)}
              {cell(r.call?.quote?.theta)}
              {cell(r.call?.quote?.vega)}
              {cell(r.call?.quote?.rho)}
              {cell(r.put?.quote?.delta)}
              {cell(r.put?.quote?.gamma, 3)}
              {cell(r.put?.quote?.theta)}
              {cell(r.put?.quote?.vega)}
              {cell(r.put?.quote?.rho)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
