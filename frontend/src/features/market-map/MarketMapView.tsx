import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ApiError, type MarketMapSnapshot, type MarketMapSector, type MapStock } from "@/types";
import { getMarketMap } from "@/api/market";
import { ANALYTICS_UNIVERSES } from "@/types";
import { useAnalyticsCoverage } from "@/features/analytics/useAnalyticsCoverage";
import { AsyncStateView, Field, Input, Select } from "@/components/ui";
import { fmtInt } from "@/utils/format";

type MapSizeMode = "equal" | "volume";
type MapMetric = "price" | "volume";

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface PlacedTile {
  stock: MapStock;
  rect: Rect;
}

interface PlacedSector {
  sector: MarketMapSector;
  rect: Rect;
  headerH: number;
  tiles: PlacedTile[];
}

// Bounded log weight so a single high-volume stock cannot dominate the map.
function volumeWeight(volume: number | null): number {
  if (!volume || volume <= 0) return 1;
  return Math.max(0.3, Math.min(3, (Math.log10(volume) - 3) / 2));
}

// Normalized [0..1] volume intensity across the visible set, so the Volume
// metric colors relative to the current universe (not an absolute constant).
function makeVolNorm(stocks: MapStock[]): (v: number | null) => number {
  const vols = stocks.map((s) => s.volume ?? 0).filter((v) => v > 0);
  if (!vols.length) return () => 0;
  const min = Math.log10(Math.max(1, Math.min(...vols)));
  const max = Math.log10(Math.max(1, Math.max(...vols)));
  const span = Math.max(0.0001, max - min);
  return (v) => {
    if (!v || v <= 0) return 0;
    return Math.max(0, Math.min(1, (Math.log10(v) - min) / span));
  };
}

// Squarified treemap (Bruls, Huizing & van Wijk). Returns one rect per weight,
// preserving input order. Pure layout — no data semantics.
function squarify(weights: number[], rect: Rect): Rect[] {
  const total = weights.reduce((a, b) => a + b, 0);
  const area = rect.w * rect.h;
  const result: Rect[] = new Array(weights.length);
  if (total <= 0 || area <= 0) {
    return weights.map(() => ({ x: rect.x, y: rect.y, w: 0, h: 0 }));
  }
  const scale = area / total;
  const items = weights.map((wt, i) => ({ i, area: wt * scale }));
  items.sort((a, b) => b.area - a.area);

  let free: Rect = { ...rect };
  let row: typeof items = [];

  const worst = (r: typeof items, side: number): number => {
    if (r.length === 0) return Infinity;
    let sum = 0;
    let max = -Infinity;
    let min = Infinity;
    for (const it of r) {
      sum += it.area;
      if (it.area > max) max = it.area;
      if (it.area < min) min = it.area;
    }
    const s2 = sum * sum;
    const side2 = side * side;
    return Math.max((side2 * max) / s2, s2 / (side2 * min));
  };

  const layout = (r: typeof items) => {
    const sum = r.reduce((a, it) => a + it.area, 0);
    if (free.w >= free.h) {
      const colW = sum / free.h;
      let y = free.y;
      for (const it of r) {
        const hh = it.area / colW;
        result[it.i] = { x: free.x, y, w: colW, h: hh };
        y += hh;
      }
      free = { x: free.x + colW, y: free.y, w: Math.max(0, free.w - colW), h: free.h };
    } else {
      const rowH = sum / free.w;
      let x = free.x;
      for (const it of r) {
        const ww = it.area / rowH;
        result[it.i] = { x, y: free.y, w: ww, h: rowH };
        x += ww;
      }
      free = { x: free.x, y: free.y + rowH, w: free.w, h: Math.max(0, free.h - rowH) };
    }
  };

  for (const it of items) {
    const next = [...row, it];
    const side = Math.min(free.w, free.h);
    if (row.length === 0 || worst(row, side) >= worst(next, side)) {
      row = next;
    } else {
      layout(row);
      row = [it];
    }
  }
  if (row.length) layout(row);
  return result;
}

function useElementSize() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 0, h: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const first = entries[0];
      if (!first) return;
      const cr = first.contentRect;
      setSize({ w: cr.width, h: cr.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, size] as const;
}

function fmtChange(v: number | null): string {
  if (v === null || v === undefined) return "—";
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function tileFill(st: MapStock, metric: MapMetric, volNorm: (v: number | null) => number): string {
  if (st.status === "unavailable") return "var(--surface-2)";
  if (metric === "volume") {
    const t = volNorm(st.volume ?? null);
    return `color-mix(in oklch, var(--accent) ${12 + t * 70}%, var(--surface-2))`;
  }
  const pct = st.change_percent ?? 0;
  const mag = Math.max(0, Math.min(1, Math.abs(pct) / 5));
  const dir = pct >= 0 ? "var(--pos)" : "var(--neg)";
  return `color-mix(in oklch, ${dir} ${22 + mag * 70}%, var(--surface-2))`;
}

function tileTextColor(st: MapStock, metric: MapMetric, volNorm: (v: number | null) => number): string {
  if (st.status === "unavailable") return "var(--text-muted)";
  if (metric === "volume") {
    return volNorm(st.volume ?? null) > 0.45 ? "var(--text-inverse)" : "var(--text)";
  }
  const mag = Math.max(0, Math.min(1, Math.abs(st.change_percent ?? 0) / 5));
  return mag > 0.45 ? "var(--text-inverse)" : "var(--text)";
}

export function MarketMapView() {
  const navigate = useNavigate();
  const [universe, setUniverse] = useState<string>("FNO");
  const [sizeMode, setSizeMode] = useState<MapSizeMode>("equal");
  const [metric, setMetric] = useState<MapMetric>("price");
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

  const volNorm = useMemo(
    () => makeVolNorm(filteredSectors.flatMap((s) => s.stocks)),
    [filteredSectors],
  );

  const [mapRef, { w: width, h: height }] = useElementSize();

  const layout = useMemo<PlacedSector[]>(() => {
    if (!width || !height || !filteredSectors.length) return [];
    const HEADER_MIN = 16;
    const HEADER_MAX = 26;

    const sectorWeights = filteredSectors.map((sg) =>
      sizeMode === "volume"
        ? sg.stocks.reduce((a, st) => a + volumeWeight(st.volume), 0)
        : sg.stocks.length,
    );
    const sectorRects = squarify(sectorWeights, { x: 0, y: 0, w: width, h: height });

    return filteredSectors.map((sg, idx) => {
      const sr = sectorRects[idx]!;
      const headerH = Math.min(HEADER_MAX, Math.max(HEADER_MIN, sr.h * 0.12));
      const inner: Rect = {
        x: sr.x,
        y: sr.y + headerH,
        w: sr.w,
        h: Math.max(0, sr.h - headerH),
      };
      const stockWeights = sg.stocks.map((st) =>
        sizeMode === "volume" ? volumeWeight(st.volume) : 1,
      );
      const tileRects =
        inner.w > 2 && inner.h > 2 ? squarify(stockWeights, inner) : [];
      const tiles = sg.stocks.map((st, i) => ({ stock: st, rect: tileRects[i]! }));
      return { sector: sg, rect: sr, headerH, tiles };
    });
  }, [filteredSectors, sizeMode, width, height]);

  const openStock = (st: MapStock) => {
    // Preserve EXACT instrument identity (never substitute futures for cash).
    const q = new URLSearchParams();
    if (st.instrument_token) q.set("key", st.instrument_token);
    q.set("sym", st.symbol);
    q.set("ex", st.exchange);
    q.set("type", "EQUITY");
    navigate(`/charts?${q.toString()}`);
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
    body = (
        <div className="heatmap-surface">
          <div className="heatmap-bar">
            <span className="heatmap-title">
              Market Heatmap
              {data && (
                <span className="heatmap-meta">
                  {data.universe} · {data.sectors.length} sectors ·{" "}
                  {data.unclassified} unclassified
                  {data.stale ? " · stale (last session)" : ""}
                </span>
              )}
            </span>
          <div className="heat-legend" aria-label={`${metric} color scale`}>
            {metric === "price" ? (
              <>
                <span className="heat-scale" aria-hidden="true" />
                <span className="heat-scale-labels">
                  <span>-5%</span>
                  <span>0</span>
                  <span>+5%</span>
                </span>
              </>
            ) : (
              <>
                <span className="heat-scale heat-scale-vol" aria-hidden="true" />
                <span className="heat-scale-labels">
                  <span>low</span>
                  <span>high</span>
                </span>
              </>
            )}
            <span className="chip chip-off">n/a</span>
          </div>
        </div>
        <div className="mktreemap" ref={mapRef}>
          {layout.map((sec) => (
            <div key={sec.sector.sector} className="mk-sector">
              <div
                className="mk-sector-label"
                style={{
                  left: sec.rect.x,
                  top: sec.rect.y,
                  width: sec.rect.w,
                  height: sec.headerH,
                }}
              >
                <span className="mk-sector-name">{sec.sector.sector}</span>
                <span className="mk-sector-count">{sec.tiles.length}</span>
              </div>
              {sec.tiles.map((t) => {
                const r = t.rect;
                if (!r || r.w <= 0 || r.h <= 0) return null;
                const GAP = 4;
                const style: React.CSSProperties = {
                  left: r.x + GAP / 2,
                  top: r.y + GAP / 2,
                  width: Math.max(0, r.w - GAP),
                  height: Math.max(0, r.h - GAP),
                  background: tileFill(t.stock, metric, volNorm),
                  color: tileTextColor(t.stock, metric, volNorm),
                };
                return (
                  <button
                    type="button"
                    key={`${t.stock.exchange}:${t.stock.symbol}`}
                    className="mk-tile"
                    style={style}
                    title={`${t.stock.symbol} · ${fmtChange(t.stock.change_percent)}`}
                    onClick={() => openStock(t.stock)}
                  >
                    <span className="mk-sym">{t.stock.symbol}</span>
                    <span className="mk-chg">{fmtChange(t.stock.change_percent)}</span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    );
  }

  const sectorOptions = useMemo(() => {
    if (!data) return [];
    return data.sectors
      .filter((sg) => sg.sector !== "Unclassified")
      .map((sg) => sg.sector)
      .sort((a, b) => a.localeCompare(b));
  }, [data]);

  return (
    <div className="market-map">
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
        <Field label="Metric">
          <Select
            value={metric}
            onChange={(e) => setMetric(e.target.value as MapMetric)}
          >
            <option value="price">Price %</option>
            <option value="volume">Volume</option>
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
            className="filter-input"
            placeholder="Symbol…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
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
