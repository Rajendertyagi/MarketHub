import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, type SectorHeatmapSnapshot } from "@/types";
import { getSectorHeatmap } from "@/api/market";
import { ANALYTICS_UNIVERSES } from "@/types";
import { useAnalyticsCoverage } from "@/features/analytics/useAnalyticsCoverage";
import { buildSectorHeatmapOption } from "./sectorHeatmapOption";
import { EChart } from "@/components/EChart";
import { AsyncStateView, Field, Select } from "@/components/ui";
import { fmtInt } from "@/utils/format";

export function SectorHeatmapView() {
  const [universe, setUniverse] = useState<string>("NIFTY50");

  useAnalyticsCoverage(universe);

  const query = useQuery({
    queryKey: ["sector-heatmap", universe],
    queryFn: ({ signal }) => getSectorHeatmap(universe, signal),
    refetchInterval: 5000,
  });

  const data: SectorHeatmapSnapshot | undefined = query.data;
  const option = useMemo(() => (data ? buildSectorHeatmapOption(data) : null), [data]);

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
    body = option ? <EChart option={option} /> : null;
  }

  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">Sector Heatmap</h1>
        {data && (
          <span className="muted">
            {data.universe} · {data.sector_count} sectors ·{" "}
            {data.classified_count} classified / {data.unclassified_count} unclassified ·{" "}
            {data.weighting}-weighted
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

      {body}
    </div>
  );
}
