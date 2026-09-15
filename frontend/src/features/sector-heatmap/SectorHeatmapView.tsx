import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, type SectorHeatmapSnapshot, type SectorRow } from "@/types";
import { getSectorHeatmap } from "@/api/market";
import { ANALYTICS_UNIVERSES } from "@/types";
import { useAnalyticsCoverage } from "@/features/analytics/useAnalyticsCoverage";
import { buildSectorHeatmapOption } from "./sectorHeatmapOption";
import { EChart } from "@/components/EChart";
import { AsyncStateView, Field, Input, Select } from "@/components/ui";
import { fmtInt, fmtPct, tone } from "@/utils/format";

export function SectorHeatmapView() {
  const [universe, setUniverse] = useState<string>("NIFTY50");
  const [sectorSearch, setSectorSearch] = useState<string>("");
  const [selected, setSelected] = useState<string | null>(null);

  useAnalyticsCoverage(universe);

  const query = useQuery({
    queryKey: ["sector-heatmap", universe],
    queryFn: ({ signal }) => getSectorHeatmap(universe, signal),
    refetchInterval: 5000,
  });

  const data: SectorHeatmapSnapshot | undefined = query.data;
  const option = useMemo(() => (data ? buildSectorHeatmapOption(data) : null), [data]);

  const sectorRows = useMemo(() => {
    if (!data) return [];
    const q = sectorSearch.trim().toUpperCase();
    return [...data.sectors]
      .filter((s) => !q || s.sector.toUpperCase().includes(q))
      .sort(
        (a, b) =>
          (b.average_change_percent ?? -Infinity) -
          (a.average_change_percent ?? -Infinity),
      );
  }, [data, sectorSearch]);

  let body: React.ReactNode;
  if (query.isLoading) {
    body = <AsyncStateView status="loading" loadingLabel="Loading sector heatmap…" />;
  } else if (query.isError) {
    body = (
      <AsyncStateView
        status="error"
        error={query.error as ApiError}
        onRetry={() => query.refetch()}
      />
    );
  } else if (!data || data.sector_count === 0) {
    body = (
      <AsyncStateView
        status="empty"
        emptyLabel={`No sectors for ${universe}.`}
      />
    );
  } else {
    body = (
      <div className="card">
        <div className="panel-header">
          <h2>Sector Heatmap</h2>
          <div className="heat-legend" aria-label="Change % color scale">
            <span className="heat-scale" aria-hidden="true" />
            <span className="heat-scale-labels">
              <span>-5%</span>
              <span>0</span>
              <span>+5%</span>
            </span>
            <span className="chip chip-off">n/a</span>
          </div>
        </div>
        {option ? (
          <EChart option={option} className="chart-frame chart-frame--tall" />
        ) : null}
      </div>
    );
  }

  return (
    <div className="panel sector-heatmap">
      <div className="page-header">
        <h1 className="page-title">Sector Analysis</h1>
        {data && (
          <span className="muted">
            {data.universe} · {data.sector_count} sectors ·{" "}
            {data.classified_count} classified / {data.unclassified_count} unclassified ·{" "}
            {data.weighting}-weighted
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
        {selected && (
          <button
            type="button"
            className="sector-clear"
            onClick={() => setSelected(null)}
          >
            Clear selection · {selected}
          </button>
        )}
      </div>

      {data && (
        <div className="stat-grid">
          <div className="stat-card">
            <span className="stat-label">Advances</span>
            <span className="stat-value pos">{fmtInt(data.advances)}</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Declines</span>
            <span className="stat-value neg">{fmtInt(data.declines)}</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Quoted</span>
            <span className="stat-value">{fmtInt(data.quoted)}</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Unavailable</span>
            <span className="stat-value muted">{fmtInt(data.unavailable)}</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Unclassified</span>
            <span className="stat-value muted">{fmtInt(data.unclassified_count)}</span>
          </div>
        </div>
      )}

      {data && sectorRows.length > 0 && (
        <section className="panel">
          <div className="panel-header">
            <h2>Sector Performance</h2>
            <Input
              className="filter-input"
              placeholder="Filter sectors…"
              value={sectorSearch}
              onChange={(e) => setSectorSearch(e.target.value)}
            />
          </div>

          <div
            className="sector-grid"
            role="list"
            aria-label="Sector performance cards"
          >
            {sectorRows.map((s) => (
              <SectorCard
                key={s.sector}
                sector={s}
                selected={selected === s.sector}
                onSelect={() =>
                  setSelected((cur) => (cur === s.sector ? null : s.sector))
                }
              />
            ))}
          </div>
        </section>
      )}

      {body}
    </div>
  );
}

function SectorCard({
  sector,
  selected,
  onSelect,
}: {
  sector: SectorRow;
  selected: boolean;
  onSelect: () => void;
}) {
  const chg = sector.average_change_percent;
  const chgTone = tone(chg);
  const gainer = mover(sector.top_gainer);
  const loser = mover(sector.top_loser);

  const advPct =
    sector.advances + sector.declines + sector.unchanged > 0
      ? (sector.advances /
          (sector.advances + sector.declines + sector.unchanged)) *
        100
      : 0;
  const decPct =
    sector.advances + sector.declines + sector.unchanged > 0
      ? (sector.declines /
          (sector.advances + sector.declines + sector.unchanged)) *
        100
      : 0;

  return (
    <button
      type="button"
      role="listitem"
      className={`sector-card${selected ? " is-selected" : ""}`}
      onClick={onSelect}
      aria-pressed={selected}
    >
      <div className="sector-card__head">
        <span className="sector-card__name" title={sector.sector}>
          {sector.sector}
        </span>
        <span className="sector-card__count muted">
          {fmtInt(sector.constituent_count)} names
        </span>
      </div>

      <div className={`sector-card__chg ${chgTone}`}>
        {chg != null ? fmtPct(chg) : "—"}
        <span className="sector-card__chg-label muted">avg chg</span>
      </div>

      <div
        className="sector-card__advdec"
        role="img"
        aria-label={`${fmtInt(sector.advances)} advancing, ${fmtInt(
          sector.declines,
        )} declining, ${fmtInt(sector.unchanged)} unchanged`}
      >
        <div className="sector-card__advdec-bar">
          <span
            className="seg seg-pos"
            style={{ width: `${advPct}%` }}
            title={`${fmtInt(sector.advances)} advancing`}
          />
          <span
            className="seg seg-neg"
            style={{ width: `${decPct}%` }}
            title={`${fmtInt(sector.declines)} declining`}
          />
          <span
            className="seg seg-flat"
            style={{ width: `${100 - advPct - decPct}%` }}
            title={`${fmtInt(sector.unchanged)} unchanged`}
          />
        </div>
        <div className="sector-card__advdec-legend">
          <span className="pos">{fmtInt(sector.advances)} adv</span>
          <span className="neg">{fmtInt(sector.declines)} dec</span>
          <span className="muted">{fmtInt(sector.unchanged)} unch</span>
        </div>
      </div>

      <ConstituentBars members={sector.members} />

      <div className="sector-card__movers">
        <div className="mover">
          <span className="mover-label muted">Top ↑</span>
          <span className="mover-sym">{gainer.symbol}</span>
          <span className={`mover-chg pos`}>{gainer.change}</span>
        </div>
        <div className="mover">
          <span className="mover-label muted">Top ↓</span>
          <span className="mover-sym">{loser.symbol}</span>
          <span className="mover-chg neg">{loser.change}</span>
        </div>
      </div>
    </button>
  );
}

function ConstituentBars({
  members,
}: {
  members: SectorRow["members"];
}) {
  const bars = useMemo(() => {
    const valid = members.filter(
      (m) => typeof m.change_percent === "number",
    ) as (typeof members[number] & { change_percent: number })[];
    const maxAbs = valid.reduce(
      (m, x) => Math.max(m, Math.abs(x.change_percent)),
      0,
    );
    return valid.map((m) => {
      const mag =
        maxAbs > 0 ? Math.abs(m.change_percent) / maxAbs : 0;
      const height = 12 + mag * 88; // 12%–100% of the track
      return {
        symbol: m.symbol,
        height,
        tone: m.change_percent > 0 ? "pos" : "neg",
        label: `${m.symbol} ${fmtPct(m.change_percent)}`,
      };
    });
  }, [members]);

  if (bars.length === 0) {
    return (
      <div className="sector-card__bars sector-card__bars--empty muted">
        no quoted constituents
      </div>
    );
  }

  return (
    <div className="sector-card__bars" aria-hidden="true">
      {bars.map((b) => (
        <span
          key={b.symbol}
          className={`bar ${b.tone}`}
          style={{ height: `${b.height}%` }}
          title={b.label}
        />
      ))}
    </div>
  );
}

function mover(r: Record<string, unknown> | null): {
  symbol: string;
  change: string;
} {
  if (!r) return { symbol: "—", change: "—" };
  const symbol = typeof r.symbol === "string" && r.symbol ? r.symbol : "—";
  const chg = typeof r.change_percent === "number" ? fmtPct(r.change_percent) : "—";
  return { symbol, change: chg };
}
