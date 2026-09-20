import { type ReactNode, useMemo } from "react";
import { Link } from "react-router-dom";
import type { ChainLegView, ChainRowView } from "@/types";
import { fmtInt, fmtIv, fmtNum, tone } from "@/utils/format";
import { chartHref } from "./chartNav";
import { askOf, bidOf } from "./useFno";

interface Props {
  rows: ChainRowView[];
  spot?: number | null;
  showGreeks?: boolean;
}

const BASE_HEAD = ["OI", "OIΔ", "Vol", "IV", "LTP", "Bid", "Ask"];
const GREEK_HEAD = ["Δ", "Γ", "Θ", "Vega", "ρ"];

function heatStyle(max: number, value: number | null | undefined): React.CSSProperties {
  const pct = max > 0 && value ? Math.min(100, (value / max) * 100) : 0;
  return { width: `${pct}%` };
}

// CE | strike | PE ladder. Canonical IV fraction is formatted as % (never
// re-divided); greeks pass through; missing quote fields render "—" (never
// fabricated). The LTP cell links to Charts preserving the exact option identity.
// OI/Volume cells carry a heat-bar scaled to the max across the visible chain so
// concentration is visible at a glance; ITM legs are tinted per side.
export function OptionChainTable({ rows, spot, showGreeks = true }: Props) {
  const { maxOi, maxVol } = useMemo(() => {
    let oi = 0;
    let vol = 0;
    for (const r of rows) {
      for (const leg of [r.call, r.put]) {
        const q = leg?.quote;
        if (q?.open_interest) oi = Math.max(oi, q.open_interest);
        if (q?.volume) vol = Math.max(vol, q.volume);
      }
    }
    return { maxOi: oi, maxVol: vol };
  }, [rows]);

  if (!rows.length) {
    return (
      <div className="state">
        <span className="muted">No option contracts listed for this expiry.</span>
      </div>
    );
  }

  const head = showGreeks ? [...BASE_HEAD, ...GREEK_HEAD] : BASE_HEAD;

  return (
    <div className="card fno-table-card">
      <table className="table option-chain">
        <thead>
          <tr>
            {head.map((h) => (
              <th key={`ce-${h}`} className="num ce-col">
                {h}
              </th>
            ))}
            <th className="strike-col">Strike</th>
            {head.map((h) => (
              <th key={`pe-${h}`} className="num pe-col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const ceItm = spot != null && r.strike < spot;
            const peItm = spot != null && r.strike > spot;
            const rowCls = [
              r.atm ? "option-chain-atm" : "",
              ceItm ? "ce-itm" : "",
              peItm ? "pe-itm" : "",
            ]
              .join(" ")
              .trim();
            return (
              <tr key={r.strike} className={rowCls}>
                {legCells(r.call, "ce", maxOi, maxVol, showGreeks)}
                <td className="strike-col">
                  <b>{fmtNum(r.strike)}</b>
                  {r.atm && <span className="chip chip-atm">ATM</span>}
                </td>
                {legCells(r.put, "pe", maxOi, maxVol, showGreeks)}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function legCells(
  leg: ChainLegView | undefined,
  side: "ce" | "pe",
  maxOi: number,
  maxVol: number,
  showGreeks: boolean,
): ReactNode {
  const sideCls = side === "ce" ? "ce-cell" : "pe-cell";
  if (!leg) {
    return (
      <td className={`num ${sideCls}`} colSpan={showGreeks ? 12 : 7}>
        —
      </td>
    );
  }
  const q = leg.quote;
  const t = tone(q?.change);
  const oi = q?.open_interest;
  const vol = q?.volume;
  const cells: ReactNode[] = [
    <td key="oi" className={`num oi-cell has-heat ${sideCls}`}>
      <span className="heat-bar" style={heatStyle(maxOi, oi)} />
      <span className="heat-val">{fmtInt(oi)}</span>
    </td>,
    <td key="oic" className={`num ${tone(q?.oi_change)} ${sideCls}`}>
      {fmtInt(q?.oi_change)}
    </td>,
    <td key="vol" className={`num vol-cell has-heat ${sideCls}`}>
      <span className="heat-bar" style={heatStyle(maxVol, vol)} />
      <span className="heat-val">{fmtInt(vol)}</span>
    </td>,
    <td key="iv" className={`num ${sideCls}`}>
      {fmtIv(q?.iv)}
    </td>,
    <td key="ltp" className={`num ${t} ${sideCls}`}>
      {q?.ltp == null ? (
        "—"
      ) : (
        <Link
          className="link"
          to={chartHref(leg.key, leg.label, leg.exchange, "OPTION")}
          title={`Chart ${leg.label}`}
        >
          {fmtNum(q.ltp)}
        </Link>
      )}
    </td>,
    <td key="bid" className={`num ${sideCls}`}>
      {fmtNum(bidOf(q))}
    </td>,
    <td key="ask" className={`num ${sideCls}`}>
      {fmtNum(askOf(q))}
    </td>,
  ];
  if (showGreeks) {
    cells.push(
      <td key="d" className={`num ${sideCls}`}>
        {fmtNum(q?.delta)}
      </td>,
      <td key="g" className={`num ${sideCls}`}>
        {fmtNum(q?.gamma, 3)}
      </td>,
      <td key="th" className={`num ${sideCls}`}>
        {fmtNum(q?.theta)}
      </td>,
      <td key="v" className={`num ${sideCls}`}>
        {fmtNum(q?.vega)}
      </td>,
      <td key="r" className={`num ${sideCls}`}>
        {fmtNum(q?.rho)}
      </td>,
    );
  }
  return <>{cells}</>;
}
