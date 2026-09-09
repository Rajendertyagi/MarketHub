import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, type BreadthSnapshot, type BreadthStatus } from "@/types";
import { getBreadth } from "@/api/market";
import { ANALYTICS_UNIVERSES } from "@/types";
import { useAnalyticsCoverage } from "@/features/analytics/useAnalyticsCoverage";
import { buildBreadthBarOption } from "./breadthOption";
import { EChart } from "@/components/EChart";
import { AsyncStateView, Field, Select } from "@/components/ui";
import { fmtInt, fmtNum, fmtPct } from "@/utils/format";

type StatusFilter = "all" | BreadthStatus;
type SortKey = "symbol" | "change_percent" | "change" | "ltp";

function StatCard({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" | "muted" }) {
  return (
    <div className="stat-card">
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${tone ?? ""}`}>{value}</span>
    </div>
  );
}

export function BreadthView() {
  const [universe, setUniverse] = useState<string>("NIFTY50");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("symbol");

  useAnalyticsCoverage(universe);

  const query = useQuery({
    queryKey: ["breadth", universe],
    queryFn: ({ signal }) => getBreadth(universe, signal),
    refetchInterval: 5000,
  });

  const data: BreadthSnapshot | undefined = query.data;
  const option = useMemo(() => (data ? buildBreadthBarOption(data) : null), [data]);

  const rows = useMemo(() => {
    if (!data) return [];
    let r = data.rows.slice();
    if (statusFilter !== "all") r = r.filter((x) => x.status === statusFilter);
    r.sort((a, b) => {
      if (sortKey === "symbol") return a.symbol.localeCompare(b.symbol);
      const av = a[sortKey] ?? -Infinity;
      const bv = b[sortKey] ?? -Infinity;
      return bv - av;
    });
    return r;
  }, [data, statusFilter, sortKey]);

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
    body = (
      <AsyncStateView
        status="empty"
        emptyLabel={`No constituents for ${universe}.`}
      />
    );
  } else {
    body = (
      <div className="card" style={{ padding: 0, overflow: "auto", maxHeight: 460 }}>
        <table className="table">
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
                    <span className={r.status === "unavailable" ? "chip chip-off" : "chip chip-on"}>
                      {r.status}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">Market Breadth</h1>
        {data && (
          <span className="muted">
            {data.universe} · {data.eligible} eligible · {data.quoted} quoted ·{" "}
            {data.unclassified} unclassified
            {data.stale ? " · stale (last session)" : ""}
          </span>
        )}
      </div>

      <div className="control-row">
        <Field label="Universe">
          <Select value={universe} onChange={(e) => setUniverse(e.target.value)}>
            {ANALYTICS_UNIVERSES.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Status">
          <Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}>
            <option value="all">All</option>
            <option value="advance">Advance</option>
            <option value="decline">Decline</option>
            <option value="unchanged">Unchanged</option>
            <option value="unavailable">Unavailable</option>
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
      </div>

      {data && (
        <>
          <div className="stat-grid">
            <StatCard label="Advances" value={fmtInt(data.advances)} tone="pos" />
            <StatCard label="Declines" value={fmtInt(data.declines)} tone="neg" />
            <StatCard label="Unchanged" value={fmtInt(data.unchanged)} />
            <StatCard
              label="Unavailable"
              value={fmtInt(data.unavailable)}
              tone={data.unavailable ? "muted" : undefined}
            />
            <StatCard label="Advance %" value={fmtPct(data.advance_percent)} tone="pos" />
            <StatCard label="Decline %" value={fmtPct(data.decline_percent)} tone="neg" />
            <StatCard label="Net Advances" value={fmtInt(data.net_advances)} />
            <StatCard
              label="A/D Ratio"
              value={data.ad_ratio === null ? "∞" : data.ad_ratio.toFixed(2)}
            />
            <StatCard label="Eligible" value={fmtInt(data.eligible)} />
            <StatCard label="Quoted" value={fmtInt(data.quoted)} />
          </div>

          <div className="card">
            <div className="hint ok">
              {data.weighting}-weighted · as of {data.as_of ?? "—"}
            </div>
            {option && <EChart option={option} style={{ height: 120 }} />}
          </div>
        </>
      )}

      {body}
    </div>
  );
}
