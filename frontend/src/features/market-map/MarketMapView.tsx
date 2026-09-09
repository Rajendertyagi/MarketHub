import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { ECharts } from "echarts";
import { ApiError, type MarketMapSnapshot, type MarketMapSector } from "@/types";
import { getMarketMap } from "@/api/market";
import { ANALYTICS_UNIVERSES } from "@/types";
import { useAnalyticsCoverage } from "@/features/analytics/useAnalyticsCoverage";
import {
  buildMarketMapOption,
  type MapSizeMode,
} from "./marketMapOption";
import { EChart } from "@/components/EChart";
import { AsyncStateView, Field, Input, Select } from "@/components/ui";
import { fmtInt } from "@/utils/format";

interface StockMeta {
  symbol: string;
  exchange: string;
  token: string | null;
  change: number | null;
  status: string;
  fno: boolean;
}

export function MarketMapView() {
  const navigate = useNavigate();
  const [universe, setUniverse] = useState<string>("FNO");
  const [sizeMode, setSizeMode] = useState<MapSizeMode>("equal");
  const [sectorFilter, setSectorFilter] = useState<string>("");
  const [movement, setMovement] = useState<string>("all");
  const [search, setSearch] = useState<string>("");

  useAnalyticsCoverage(universe);

  const query = useQuery({
    queryKey: ["market-map", universe],
    queryFn: ({ signal }) => getMarketMap(universe, signal),
    refetchInterval: 5000,
  });

  const data: MarketMapSnapshot | undefined = query.data;

  const filteredSectors = useMemo<MarketMapSector[]>(() => {
    if (!data) return [];
    const q = search.trim().toUpperCase();
    return data.sectors
      .filter((sg) => !sectorFilter || sg.sector === sectorFilter)
      .map((sg) => ({
        ...sg,
        stocks: sg.stocks.filter((st) => {
          if (movement !== "all" && st.status !== movement) return false;
          if (q && !st.symbol.toUpperCase().includes(q)) return false;
          return true;
        }),
      }))
      .filter((sg) => sg.stocks.length > 0);
  }, [data, sectorFilter, movement, search]);

  const option = useMemo(
    () => buildMarketMapOption(filteredSectors, sizeMode),
    [filteredSectors, sizeMode],
  );

  const onChartInit = (inst: ECharts) => {
    inst.on("click", (params: unknown) => {
      const d = params as { data?: { _meta?: StockMeta } };
      const meta = d.data?._meta;
      if (!meta) return; // sector node -> handled by zoomToNode
      // Preserve EXACT instrument identity (never substitute futures for cash).
      // Navigate to Charts with the canonical key/exchange/symbol; ChartsView
      // hydrates directly (or resolves via the catalog when the token is absent).
      const q = new URLSearchParams();
      if (meta.token) q.set("key", meta.token);
      q.set("sym", meta.symbol);
      q.set("ex", meta.exchange);
      q.set("type", "EQUITY");
      navigate(`/charts?${q.toString()}`);
    });
  };

  let body: React.ReactNode;
  if (query.isLoading) {
    body = <AsyncStateView status="loading" loadingLabel="Loading market map…" />;
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
      <AsyncStateView status="empty" emptyLabel={`No constituents for ${universe}.`} />
    );
  } else {
    body = <EChart option={option} onInit={onChartInit} />;
  }

  const sectorOptions = useMemo(() => {
    if (!data) return [];
    return data.sectors
      .filter((sg) => sg.sector !== "Unclassified")
      .map((sg) => sg.sector)
      .sort((a, b) => a.localeCompare(b));
  }, [data]);

  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">Market Map</h1>
        {data && (
          <span className="muted">
            {data.universe} · {data.sectors.length} sectors ·{" "}
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
        <Field label="Tile Size">
          <Select
            value={sizeMode}
            onChange={(e) => setSizeMode(e.target.value as MapSizeMode)}
          >
            <option value="equal">Equal</option>
            <option value="volume">Volume</option>
          </Select>
        </Field>
        <Field label="Sector">
          <Select value={sectorFilter} onChange={(e) => setSectorFilter(e.target.value)}>
            <option value="">All Sectors</option>
            {sectorOptions.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Movement">
          <Select value={movement} onChange={(e) => setMovement(e.target.value)}>
            <option value="all">All</option>
            <option value="advance">Advance</option>
            <option value="decline">Decline</option>
            <option value="unchanged">Unchanged</option>
            <option value="unavailable">Unavailable</option>
          </Select>
        </Field>
        <Field label="Search">
          <Input
            placeholder="Symbol…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: 160 }}
          />
        </Field>
      </div>

      {data && (
        <div className="stat-grid">
          <div className="stat-card">
            <span className="stat-label">Eligible</span>
            <span className="stat-value">{fmtInt(data.eligible)}</span>
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
            <span className="stat-label">Advances</span>
            <span className="stat-value pos">{fmtInt(data.advances)}</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Declines</span>
            <span className="stat-value neg">{fmtInt(data.declines)}</span>
          </div>
          <div className="stat-card">
            <span className="stat-label">Unchanged</span>
            <span className="stat-value">{fmtInt(data.unchanged)}</span>
          </div>
        </div>
      )}

      {body}
    </div>
  );
}
