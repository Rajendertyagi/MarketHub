import { useMemo } from "react";
import { EChart } from "@/components/EChart";
import type { ChainAnalytics, ChainRowView } from "@/types";
import { fmtInt, fmtNum } from "@/utils/format";
import { buildOiByStrikeOption } from "./fnoOption";

interface Props {
  analytics: ChainAnalytics;
  rows: ChainRowView[];
}

// Index-chain analytics (over the loaded window, labeled as such by the backend).
// PCR / straddle / OI are canonical; the OI-by-strike chart reuses the shared
// EChart wrapper — no second chart library, no client-side aggregation beyond
// selecting per-leg OI.
export function OptionAnalytics({ analytics, rows }: Props) {
  const option = useMemo(() => buildOiByStrikeOption(rows), [rows]);
  const pcr = analytics.pcr_by_oi;
  const straddle = useMemo(() => {
    const atm = rows.find((r) => r.atm);
    const c = atm?.call?.quote?.ltp;
    const p = atm?.put?.quote?.ltp;
    return c != null && p != null ? c + p : null;
  }, [rows]);

  return (
    <div className="fno-analytics">
      <div className="stat-grid">
        <div className="stat-card">
          <span className="stat-label">PCR (OI)</span>
          <span className="stat-value">{pcr == null ? "—" : pcr.toFixed(3)}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Total CE OI</span>
          <span className="stat-value">{fmtInt(analytics.total_call_oi)}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Total PE OI</span>
          <span className="stat-value">{fmtInt(analytics.total_put_oi)}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">ATM Straddle</span>
          <span className="stat-value">{straddle == null ? "—" : fmtNum(straddle)}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Max CE OI Strike</span>
          <span className="stat-value">
            {analytics.highest_call_oi_strike == null
              ? "—"
              : fmtNum(analytics.highest_call_oi_strike)}
          </span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Max PE OI Strike</span>
          <span className="stat-value">
            {analytics.highest_put_oi_strike == null
              ? "—"
              : fmtNum(analytics.highest_put_oi_strike)}
          </span>
        </div>
      </div>
      <div className="card">
        <div className="hint">Open Interest by Strike (loaded window)</div>
        <EChart option={option} style={{ height: 240 }} />
      </div>
    </div>
  );
}
