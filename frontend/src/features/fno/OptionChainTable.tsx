import { Link } from "react-router-dom";
import type { ChainLegView, ChainRowView } from "@/types";
import { bidOf, askOf } from "./useFno";
import { chartHref } from "./chartNav";
import { fmtInt, fmtIv, fmtNum, tone } from "@/utils/format";

interface Props {
  rows: ChainRowView[];
}

const CE_HEAD = ["OI", "OIΔ", "Vol", "IV", "LTP", "Bid", "Ask", "Δ", "Γ", "Θ", "Vega", "ρ"];
const PE_HEAD = CE_HEAD;

function legCells(leg: ChainLegView | undefined) {
  if (!leg) {
    return <td className="num" colSpan={12}>—</td>;
  }
  const q = leg.quote;
  const t = tone(q?.change);
  const ltpCell = (
    <td className={`num ${t}`}>
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
    </td>
  );
  return (
    <>
      <td className="num">{fmtInt(q?.open_interest)}</td>
      <td className={`num ${tone(q?.oi_change)}`}>{fmtInt(q?.oi_change)}</td>
      <td className="num">{fmtInt(q?.volume)}</td>
      <td className="num">{fmtIv(q?.iv)}</td>
      {ltpCell}
      <td className="num">{fmtNum(bidOf(q))}</td>
      <td className="num">{fmtNum(askOf(q))}</td>
      <td className="num">{fmtNum(q?.delta)}</td>
      <td className="num">{fmtNum(q?.gamma, 3)}</td>
      <td className="num">{fmtNum(q?.theta)}</td>
      <td className="num">{fmtNum(q?.vega)}</td>
      <td className="num">{fmtNum(q?.rho)}</td>
    </>
  );
}

// CE | strike | PE ladder. Canonical IV fraction is formatted as % (never
// re-divided); greeks are passed through; missing quote fields render "—" (never
// fabricated). The LTP cell links to Charts preserving the exact option identity.
export function OptionChainTable({ rows }: Props) {
  if (!rows.length) {
    return (
      <div className="state">
        <span className="muted">No option contracts listed for this expiry.</span>
      </div>
    );
  }
  return (
    <div className="card" style={{ padding: 0, overflow: "auto" }}>
      <table className="table option-chain">
        <thead>
          <tr>
            {CE_HEAD.map((h) => (
              <th key={`ce-${h}`} className="num ce-col">
                {h}
              </th>
            ))}
            <th className="strike-col">Strike</th>
            {PE_HEAD.map((h) => (
              <th key={`pe-${h}`} className="num pe-col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.strike} className={r.atm ? "option-chain-atm" : ""}>
              {legCells(r.call)}
              <td className="strike-col">
                <b>{fmtNum(r.strike)}</b>
                {r.atm && <span className="chip chip-atm">ATM</span>}
              </td>
              {legCells(r.put)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
