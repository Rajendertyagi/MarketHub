import { fmtNum, fmtVol, tone } from "@/utils/format";
import type { MarketQuote } from "@/features/dashboard/types";
import type { WatchlistItem } from "../types";

interface Props {
  items: WatchlistItem[];
  quotes: MarketQuote[];
  onRemove: (itemId: number) => void;
}

export function WatchlistTable({ items, quotes, onRemove }: Props) {
  if (!items.length) {
    return (
      <table className="data-table">
        <tbody>
          <tr>
            <td colSpan={8} className="empty-row">
              No items. Add instruments from the Instruments page.
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
          <th>Volume</th>
          <th>Bid</th>
          <th>Ask</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {items.map((it) => {
          const q = quotes.find((x) => x.instrument_token === it.instrument_token);
          const t = tone(q?.change);
          return (
            <tr key={it.id}>
              <td>{it.tradingsymbol ?? it.instrument_token}</td>
              <td>{q?.ltp != null ? fmtNum(q.ltp) : "—"}</td>
              <td className={t}>{q?.change != null ? fmtNum(q.change) : "—"}</td>
              <td className={t}>
                {q?.change_percent != null ? `${fmtNum(q.change_percent)}%` : "—"}
              </td>
              <td>{q ? fmtVol(q.volume) : "—"}</td>
              <td>{q?.best_bid != null ? fmtNum(q.best_bid) : "—"}</td>
              <td>{q?.best_ask != null ? fmtNum(q.best_ask) : "—"}</td>
              <td>
                <button
                  className="btn btn-compact"
                  aria-label="Remove item"
                  onClick={() => onRemove(it.id)}
                >
                  ✕
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
