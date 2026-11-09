import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getFuturesHeatmap } from "@/api/market";
import { AsyncStateView, Field, Input, Select, Tabs } from "@/components/ui";
import type { ApiError, FuturesHeatSnapshot, FuturesHeatTile } from "@/types";
import { fmtInt, fmtPct } from "@/utils/format";
import { coverageClass, coverageLabel, isPriced, oiBasisLabel } from "./coverage";
import { type BoardMetric, FuturesBoardGrid } from "./FuturesBoardGrid";

const VIEW_TABS: { value: "grid" | "table"; label: string }[] = [
  { value: "grid", label: "Grid" },
  { value: "table", label: "Table" },
];

const BUILDUP_TABS = [
  { value: "all", label: "All" },
  { value: "long", label: "Long Buildup" },
  { value: "short", label: "Short Buildup" },
  { value: "shortcovering", label: "Short Covering" },
  { value: "longunwinding", label: "Long Unwinding" },
];

const BUILDUP_LABEL: Record<string, string | null> = {
  all: null,
  long: "Long Buildup",
  short: "Short Buildup",
  shortcovering: "Short Covering",
  longunwinding: "Long Unwinding",
};

const METRIC_TABS: { value: BoardMetric; label: string }[] = [
  { value: "price", label: "Price %" },
  { value: "oi", label: "OI %" },
];

const SORT_OPTIONS = [
  { value: "price", label: "Price % ↓" },
  { value: "oi", label: "OI % ↓" },
  { value: "symbol", label: "Symbol A–Z" },
];

function fmtNum(v: number | null | undefined): string {
  return v == null ? "—" : String(Math.round(v * 100) / 100);
}

function fmtOi(v: number | null | undefined): string {
  return v == null ? "—" : fmtInt(Math.round(v));
}

// Futures Board over the Phase 7 `/api/futures/heatmap` contract. ONE
// unfiltered snapshot is the single source; every filter/sort is a
// client-side projection (no per-underlying fetches, no broker logic).
export function FuturesHeatmapView() {
  const navigate = useNavigate();
  const [view, setView] = useState<"grid" | "table">("grid");
  const [buildup, setBuildup] = useState<string>("all");
  const [metric, setMetric] = useState<BoardMetric>("price");
  const [expiry, setExpiry] = useState<string>("latest");
  const [search, setSearch] = useState<string>("");
  const [covFilter, setCovFilter] = useState<string>("all");
  const [sort, setSort] = useState<string>("price");

  const query = useQuery({
    queryKey: ["futures-heatmap", expiry],
    queryFn: ({ signal }) => getFuturesHeatmap(expiry, "all", signal),
    refetchInterval: 5000,
  });

  const data: FuturesHeatSnapshot | undefined = query.data;

  const items = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toUpperCase();
    const label = BUILDUP_LABEL[buildup] ?? null;
    const filtered = data.items.filter(
      (t) =>
        (label === null || t.buildup === label) &&
        (q === "" || t.underlying.toUpperCase().includes(q)) &&
        (covFilter === "all" || t.coverage === covFilter),
    );
    const arr = [...filtered];
    if (sort === "symbol") {
      arr.sort((a, b) => a.underlying.localeCompare(b.underlying));
    } else if (sort === "oi") {
      arr.sort((a, b) => (b.oi_pct ?? -Infinity) - (a.oi_pct ?? -Infinity));
    } else {
      arr.sort((a, b) => (b.price_pct ?? -Infinity) - (a.price_pct ?? -Infinity));
    }
    return arr;
  }, [data, search, buildup, covFilter, sort]);

  const coverageOptions = useMemo(() => {
    const states = new Set<string>();
    for (const t of data?.items ?? []) if (t.coverage) states.add(t.coverage);
    return ["all", ...[...states].sort()];
  }, [data]);

  const openContract = (t: FuturesHeatTile) => {
    const q = new URLSearchParams({
      sym: t.symbol,
      kind: t.kind || "equity",
      tab: "futures",
      expiry: t.expiry,
    });
    navigate(`/fno?${q.toString()}`);
  };

  const coverage = data?.coverage;
  const providers = data?.providers ?? [];
  const freshness = data && (data.data_status === "stale" ? "stale (last session)" : "live");

  let board: React.ReactNode;
  if (query.isLoading) {
    board = <AsyncStateView status="loading" loadingLabel="Loading futures board…" />;
  } else if (query.isError) {
    board = (
      <AsyncStateView
        status="error"
        error={query.error as ApiError}
        onRetry={() => query.refetch()}
      />
    );
  } else if (!data || data.eligible === 0) {
    board = <AsyncStateView status="empty" emptyLabel="No futures contracts found." />;
  } else if (items.length === 0) {
    board = <AsyncStateView status="empty" emptyLabel="No contracts match the filters." />;
  } else if (view === "grid") {
    board = <FuturesBoardGrid items={items} metric={metric} onOpen={openContract} />;
  } else {
    board = (
      <div className="fboard-table-wrap">
        <table className="table table-compact">
          <thead>
            <tr>
              <th>Symbol</th>
              <th className="num">LTP</th>
              <th className="num">Price %</th>
              <th className="num">OI</th>
              <th className="num">Prev OI</th>
              <th className="num">OI Chg</th>
              <th className="num">OI %</th>
              <th>Buildup</th>
              <th className="num">Spot</th>
              <th className="num">Basis</th>
              <th>Coverage</th>
              <th>OI basis</th>
              <th>Provider</th>
            </tr>
          </thead>
          <tbody>
            {items.map((t) => (
              <tr
                key={t.instrument_key || `${t.underlying}-${t.expiry}`}
                className={isPriced(t.coverage) ? "" : "row-dim"}
                onClick={() => openContract(t)}
              >
                <td className="ticker">{t.underlying}</td>
                <td className="num">{fmtNum(t.futures_ltp)}</td>
                <td className="num">{t.price_pct == null ? "—" : fmtPct(t.price_pct)}</td>
                <td className="num">{fmtOi(t.oi)}</td>
                <td className="num">{fmtOi(t.previous_oi)}</td>
                <td className="num">{fmtOi(t.oi_change)}</td>
                <td className="num">{t.oi_pct == null ? "—" : fmtPct(t.oi_pct)}</td>
                <td>{t.buildup ?? "—"}</td>
                <td className="num">{fmtNum(t.spot_ltp)}</td>
                <td className="num">{fmtNum(t.basis)}</td>
                <td>
                  <span className={coverageClass(t.coverage)}>{coverageLabel(t.coverage)}</span>
                </td>
                <td className="muted">{oiBasisLabel(t.oi_basis)}</td>
                <td className="muted">{t.provider ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="futures-heatmap">
      <div className="control-row toolbar board-toolbar">
        <Tabs tabs={VIEW_TABS} active={view} onChange={setView} />
        <Tabs tabs={BUILDUP_TABS} active={buildup} onChange={setBuildup} />
        {view === "grid" && <Tabs tabs={METRIC_TABS} active={metric} onChange={setMetric} />}
        <Field label="Expiry">
          <Select value={expiry} onChange={(e) => setExpiry(e.target.value)}>
            <option value="latest">Latest ({data?.expiry_resolved ?? "…"})</option>
            {(data?.expiries ?? []).map((e) => (
              <option key={e} value={e}>
                {e}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Coverage">
          <Select value={covFilter} onChange={(e) => setCovFilter(e.target.value)}>
            {coverageOptions.map((s) => (
              <option key={s} value={s}>
                {s === "all" ? "All" : coverageLabel(s)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Sort">
          <Select value={sort} onChange={(e) => setSort(e.target.value)}>
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Search">
          <Input
            className="filter-input"
            placeholder="Symbol…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </Field>
      </div>

      {data && data.eligible > 0 && (
        <div className="stat-grid board-summary">
          <div className="stat-card">
            <span className="stat-label">Priced</span>
            <span className="stat-value">{fmtInt(data.quoted)}</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">No quote</span>
            <span className="stat-value muted">{fmtInt(data.unavailable)}</span>
          </div>
          {coverage && (
            <>
              <div className="stat-card">
                <span className="stat-label">Routed</span>
                <span className="stat-value">{fmtInt(coverage.routed)}</span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Desired</span>
                <span className="stat-value">{fmtInt(coverage.desired)}</span>
              </div>
              {Object.entries(coverage.states).map(([state, n]) => (
                <div className="stat-card" key={state}>
                  <span className="stat-label">{coverageLabel(state)}</span>
                  <span className="stat-value">{fmtInt(n)}</span>
                </div>
              ))}
            </>
          )}
        </div>
      )}

      <div className="heatmap-surface">
        <div className="heatmap-bar">
          <span className="heatmap-title">
            Futures Board
            {data && (
              <span className="heatmap-meta">
                {data.expiry_resolved} · {fmtInt(items.length)} shown / {fmtInt(data.eligible)}{" "}
                eligible · {freshness} · built {data.as_of ?? "—"}
              </span>
            )}
          </span>
          <div className="heat-legend">
            <span className="heat-scale" aria-hidden="true" />
            <span className="heat-scale-labels">
              <span>-5%</span>
              <span>0</span>
              <span>+5%</span>
            </span>
            <span className="chip chip-off">no data</span>
          </div>
        </div>

        {providers.length > 0 && coverage && (
          <details className="board-coverage">
            <summary>
              Coverage · {fmtInt(coverage.routed)} routed / {fmtInt(coverage.desired)} desired
              {coverage.uncovered > 0 ? ` · ${fmtInt(coverage.uncovered)} uncovered` : ""}
            </summary>
            <table className="table table-compact">
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>Status</th>
                  <th>Available</th>
                  <th className="num">Routed</th>
                  <th className="num">Budget</th>
                  <th className="num">Used</th>
                  <th className="num">Free</th>
                </tr>
              </thead>
              <tbody>
                {providers.map((p) => {
                  const cap = coverage.capacity[p.provider];
                  return (
                    <tr key={p.provider}>
                      <td>{p.label}</td>
                      <td className="muted">{p.status ?? "—"}</td>
                      <td>{p.available ? "yes" : "no"}</td>
                      <td className="num">{fmtInt(coverage.by_provider[p.provider] ?? 0)}</td>
                      <td className="num">{cap?.budget ?? "—"}</td>
                      <td className="num">{cap?.used ?? 0}</td>
                      <td className="num">{cap?.available ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {coverage.errors.length > 0 && <p className="hint err">{coverage.errors.join("; ")}</p>}
          </details>
        )}

        {board}
      </div>
    </div>
  );
}
