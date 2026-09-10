import { fmtNum, fmtVol, tone } from "@/utils/format";
import { quoteKey, type MarketQuote } from "../types";

interface Props {
  quotes: MarketQuote[];
  onSelect: (key: string) => void;
}

function cell(value: number | null | undefined, digits = 2): string {
  return value != null ? fmtNum(value, digits) : "—";
}

export function MarketsTable({ quotes, onSelect }: Props) {
  if (!quotes.length) {
    return (
      <table className="data-table">
        <tbody>
          <tr>
            <td colSpan={19} className="empty-row">
              No data
            </td>
          </tr>
        </tbody>
      </table>
    );
  }
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Symbol</th>
          <th>LTP</th>
          <th>Chg</th>
          <th>Chg%</th>
          <th>Open</th>
          <th>High</th>
          <th>Low</th>
          <th>Close</th>
          <th>ATP</th>
          <th>Vol</th>
          <th>OI</th>
          <th>OI Chg</th>
          <th>OI Chg%</th>
          <th>Bid</th>
          <th>Ask</th>
          <th>U. Ckt</th>
          <th>L. Ckt</th>
          <th>Last Trade</th>
          <th>Updated</th>
        </tr>
      </thead>
      <tbody>
        {quotes.map((q) => {
          const t = tone(q.change);
          const key = quoteKey(q);
          return (
            <tr
              key={key}
              className="cursor-pointer"
              data-key={key}
              onClick={() => onSelect(key)}
            >
              <td>{q.tradingsymbol ?? q.instrument_token}</td>
              <td>{cell(q.ltp)}</td>
              <td className={t}>{cell(q.change)}</td>
              <td className={t}>{q.change_percent != null ? `${fmtNum(q.change_percent)}%` : "—"}</td>
              <td>{cell(q.open)}</td>
              <td>{cell(q.high)}</td>
              <td>{cell(q.low)}</td>
              <td>{cell(q.close)}</td>
              <td>{cell(q.avg_trade_price)}</td>
              <td>{fmtVol(q.volume)}</td>
              <td>{fmtVol(q.open_interest)}</td>
              <td>{cell(q.oi_change, 0)}</td>
              <td>{q.oi_change_percent != null ? `${fmtNum(q.oi_change_percent)}%` : "—"}</td>
              <td>{cell(q.best_bid)}</td>
              <td>{cell(q.best_ask)}</td>
              <td>{cell(q.upper_circuit)}</td>
              <td>{cell(q.lower_circuit)}</td>
              <td>{q.last_trade_time ?? "—"}</td>
              <td>{q.received_ts ? new Date(q.received_ts).toLocaleTimeString() : "—"}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
