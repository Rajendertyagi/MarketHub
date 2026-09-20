import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { getBreadth } from "@/api/market";
import { EChart } from "@/components/EChart";
import { AsyncStateView, Field, Input, Select, Tabs } from "@/components/ui";
import { useAnalyticsCoverage } from "@/features/analytics/useAnalyticsCoverage";
import {
  ANALYTICS_UNIVERSES,
  type ApiError,
  type BreadthSnapshot,
  type BreadthStatus,
} from "@/types";
import { fmtInt, fmtNum, fmtPct, fmtVol } from "@/utils/format";
import { buildBreadthBarOption } from "./breadthOption";

type StatusFilter = "all" | BreadthStatus;
type SortKey = "symbol" | "change_percent" | "change" | "ltp";

type Tone = "pos" | "neg" | "muted" | undefined;

function StatCard({
  label,
  value,
  sub,
  tone,
  accent,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: Tone;
  accent?: boolean;
}) {
  const cls = ["stat-card", tone ? `tone-${tone}` : "", accent ? "stat-card--accent" : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={cls}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {sub && <span className="stat-sub">{sub}</span>}
    </div>
  );
}

function HeroStat({
  label,
  value,
  pct,
  tone,
}: {
  label: string;
  value: string;
  pct: string;
  tone: "pos" | "neg" | "flat";
}) {
  return (
    <div className={`hero-stat hero-stat--${tone}`}>
      <span className="hero-stat-label">{label}</span>
      <span className="hero-stat-value">{value}</span>
      <span className="hero-stat-pct">{pct}</span>
    </div>
  );
}

export function BreadthView() {
  const [universe, setUniverse] = useState<string>("NIFTY50");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("symbol");
  const [search, setSearch] = useState<string>("");

  useAnalyticsCoverage(universe);

  const query = useQuery({
    queryKey: ["breadth", universe],
    queryFn: ({ signal }) => getBreadth(universe, signal),
    refetchInterval: 5000,
  });

  const data: BreadthSnapshot | undefined = query.data;
  const option = useMemo(() => (data ? buildBreadthBarOption(data) : null), [data]);

  const unchangedShare = useMemo(() => {
    if (!data?.eligible) return "0.0%";
    return `${((data.unchanged / data.eligible) * 100).toFixed(1)}%`;
  }, [data]);

  const rows = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toUpperCase();
    let r = data.rows.filter((x) => !q || x.symbol.toUpperCase().includes(q));
    if (statusFilter !== "all") r = r.filter((x) => x.status === statusFilter);
    r.sort((a, b) => {
      if (sortKey === "symbol") return a.symbol.localeCompare(b.symbol);
      const av = a[sortKey] ?? -Infinity;
      const bv = b[sortKey] ?? -Infinity;
      return bv - av;
    });
    return r;
  }, [data, statusFilter, sortKey, search]);

  let body: React.ReactNode;
  if (query.isLoading) {
    body = <AsyncStateView status="loading" loadingLabel="Loading breadth…" />;
  } else if (query.isError) {
    body = (
      <AsyncStateView
        status="error"
        error={query.error as ApiError}
        onRetry={() => query.refetch()}
      />
    );
  } else if (!data || data.eligible === 0) {
    body = <AsyncStateView status="empty" emptyLabel={`No constituents for ${universe}.`} />;
  } else {
    body = (
      <div className="card">
        <div className="panel-header">
          <h2>Constituents</h2>
          <span className="muted">{rows.length} shown</span>
        </div>
        <div className="table-scroll">
          <table className="table table-compact">
            <thead>
              <tr>
                <th>#</th>
                <th>Symbol</th>
                <th className="num">LTP</th>
                <th className="num">Chg</th>
                <th className="num">Chg%</th>
                <th>Sector</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const chgCls = (r.change ?? 0) > 0 ? "pos" : (r.change ?? 0) < 0 ? "neg" : "";
                return (
                  <tr key={r.symbol}>
                    <td className="muted">{i + 1}</td>
                    <td>{r.symbol}</td>
                    <td className="num">{fmtNum(r.ltp)}</td>
                    <td className={`num ${chgCls}`}>{fmtNum(r.change)}</td>
                    <td className={`num ${chgCls}`}>{fmtPct(r.change_percent)}</td>
                    <td>{r.sector}</td>
                    <td>
                      <span
                        className={r.status === "unavailable" ? "chip chip-off" : "chip chip-on"}
                      >
                        {r.status}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  return (
    <div className="panel breadth">
      <div className="page-header">
        <h1 className="page-title">Market Breadth</h1>
        {data && (
          <span className="muted">
            {data.universe} · {data.eligible} eligible · {data.quoted} quoted · {data.unclassified}{" "}
            unclassified
            {data.stale ? " · stale (last session)" : ""}
          </span>
        )}
      </div>

      <div className="control-row toolbar">
        <Field label="Universe">
          <Select value={universe} onChange={(e) => setUniverse(e.target.value)}>
            {ANALYTICS_UNIVERSES.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Sort">
          <Select value={sortKey} onChange={(e) => setSortKey(e.target.value as SortKey)}>
            <option value="symbol">Symbol</option>
            <option value="change_percent">Change %</option>
            <option value="change">Change</option>
            <option value="ltp">LTP</option>
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

      {data && (
        <>
          <section className="breadth-hero card">
            <div className="hero-viz">
              <div className="panel-header">
                <h2>Breadth Split</h2>
                <div className="breadth-legend">
                  <span className="swatch pos" />
                  <span className="pos">Advance</span>
                  <span className="swatch neg" />
                  <span className="neg">Decline</span>
                  <span className="swatch flat" />
                  <span className="muted">Flat</span>
                </div>
              </div>
              <div className="hint ok">
                {data.weighting}-weighted · as of {data.as_of ?? "—"}
              </div>
              {option && <EChart option={option} className="chart-frame chart-frame--hero" />}
            </div>

            <div className="hero-stats">
              <HeroStat
                label="Advances"
                value={fmtInt(data.advances)}
                pct={fmtPct(data.advance_percent)}
                tone="pos"
              />
              <HeroStat
                label="Declines"
                value={fmtInt(data.declines)}
                pct={fmtPct(data.decline_percent)}
                tone="neg"
              />
              <HeroStat
                label="Unchanged"
                value={fmtInt(data.unchanged)}
                pct={unchangedShare}
                tone="flat"
              />
            </div>
          </section>

          <section className="breadth-metrics">
            <h3 className="section-title">Breadth Metrics</h3>
            <div className="stat-grid">
              <StatCard
                label="A/D Ratio"
                value={data.ad_ratio === null ? "∞" : data.ad_ratio.toFixed(2)}
                sub={data.ad_ratio === null ? "all declines" : "advances ÷ declines"}
                accent
              />
              <StatCard
                label="Net Advances"
                value={fmtInt(data.net_advances)}
                tone={data.net_advances > 0 ? "pos" : data.net_advances < 0 ? "neg" : "muted"}
              />
              <StatCard label="Advance %" value={fmtPct(data.advance_percent)} tone="pos" />
              <StatCard label="Decline %" value={fmtPct(data.decline_percent)} tone="neg" />
              <StatCard label="Volume Adv." value={fmtVol(data.volume_advancing)} sub="advancing" />
              <StatCard label="Volume Dec." value={fmtVol(data.volume_declining)} sub="declining" />
              <StatCard
                label="Day Highs"
                value={fmtInt(data.intraday_highs)}
                sub="new intraday high"
              />
              <StatCard
                label="Day Lows"
                value={fmtInt(data.intraday_lows)}
                sub="new intraday low"
              />
              <StatCard
                label="Unavailable"
                value={fmtInt(data.unavailable)}
                tone={data.unavailable ? "muted" : undefined}
              />
              <StatCard label="Eligible" value={fmtInt(data.eligible)} />
              <StatCard label="Quoted" value={fmtInt(data.quoted)} />
            </div>
          </section>
        </>
      )}

      <section className="breadth-constituents">
        {data && (
          <div className="section-head">
            <h3 className="section-title">Constituents</h3>
            <Tabs<StatusFilter>
              tabs={[
                { value: "all", label: "All" },
                { value: "advance", label: "Advance" },
                { value: "decline", label: "Decline" },
                { value: "unchanged", label: "Unchanged" },
                { value: "unavailable", label: "Unavailable" },
              ]}
              active={statusFilter}
              onChange={setStatusFilter}
            />
          </div>
        )}
        {body}
      </section>
    </div>
  );
}
