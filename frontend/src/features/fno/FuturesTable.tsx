import { Link } from "react-router-dom";
import type { FutureView } from "@/types";
import { bidOf, askOf } from "./useFno";
import { chartHref } from "./chartNav";
import { fmtInt, fmtNum, fmtPct, tone } from "@/utils/format";

interface Props {
  futures: FutureView[];
}

// Real FUTURE contracts only. Cash values are never substituted for futures. The
// contract symbol links to Charts preserving the exact future identity.
export function FuturesTable({ futures }: Props) {
  if (!futures.length) {
    return (
      <div className="state">
        <span className="muted">No non-expired futures in catalog.</span>
      </div>
    );
  }
  return (
    <div className="card" style={{ padding: 0, overflow: "auto" }}>
      <table className="table">
        <thead>
          <tr>
            <th>Expiry</th>
            <th>Contract</th>
            <th className="num">LTP</th>
            <th className="num">Chg</th>
            <th className="num">Chg%</th>
            <th className="num">Volume</th>
            <th className="num">Bid</th>
            <th className="num">Ask</th>
          </tr>
        </thead>
        <tbody>
          {futures.map((f) => {
            const q = f.quote;
            const t = tone(q?.change);
            return (
              <tr key={f.key}>
                <td className="muted">{f.expiry}</td>
                <td>
                  <Link
                    className="link"
                    to={chartHref(f.key, f.label, f.exchange, "FUTURE")}
                    title={`Chart ${f.label}`}
                  >
                    {f.label}
                  </Link>
                </td>
                <td className="num">{fmtNum(q?.ltp)}</td>
                <td className={`num ${t}`}>{fmtNum(q?.change)}</td>
                <td className={`num ${t}`}>{fmtPct(q?.change_percent)}</td>
                <td className="num">{fmtInt(q?.volume)}</td>
                <td className="num">{fmtNum(bidOf(q))}</td>
                <td className="num">{fmtNum(askOf(q))}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
