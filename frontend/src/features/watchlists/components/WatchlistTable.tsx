import { useMemo, useState } from "react";
import { fmtNum, fmtVol, tone } from "@/utils/format";
import type { MarketQuote } from "@/features/dashboard/types";
import type { WatchlistItem } from "../types";

interface Props {
  items: WatchlistItem[];
  quotes: MarketQuote[];
  onRemove: (itemId: number) => void;
}

export function WatchlistTable({ items, quotes, onRemove }: Props) {
  const [query, setQuery] = useState("");

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((it) =>
      (it.tradingsymbol ?? it.instrument_token).toLowerCase().includes(q),
    );
  }, [items, query]);

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
    <>
      <div className="toolbar watchlist-toolbar">
        <input
          type="search"
          className="filter-input"
          placeholder="Filter symbol…"
          aria-label="Filter watchlist symbols"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span className="muted watchlist-count">
          {visible.length} / {items.length}
        </span>
      </div>
      <div className="watchlist-table-wrap">
        <table className="data-table table-compact">
          <thead>
            <tr>
              <th>Symbol</th>
              <th className="num">LTP</th>
              <th className="num">Chg</th>
              <th className="num">Chg%</th>
              <th className="num">Volume</th>
              <th className="num">Bid</th>
              <th className="num">Ask</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {visible.map((it) => {
              const q = quotes.find(
                (x) => x.instrument_token === it.instrument_token,
              );
              const t = tone(q?.change);
              return (
                <tr key={it.id}>
                  <td>{it.tradingsymbol ?? it.instrument_token}</td>
                  <td className="num">{q?.ltp != null ? fmtNum(q.ltp) : "—"}</td>
                  <td className={`num ${t}`}>
                    {q?.change != null ? fmtNum(q.change) : "—"}
                  </td>
                  <td className={`num ${t}`}>
                    {q?.change_percent != null
                      ? `${fmtNum(q.change_percent)}%`
                      : "—"}
                  </td>
                  <td className="num">{q ? fmtVol(q.volume) : "—"}</td>
                  <td className="num">
                    {q?.best_bid != null ? fmtNum(q.best_bid) : "—"}
                  </td>
                  <td className="num">
                    {q?.best_ask != null ? fmtNum(q.best_ask) : "—"}
                  </td>
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
      </div>
    </>
  );
}
