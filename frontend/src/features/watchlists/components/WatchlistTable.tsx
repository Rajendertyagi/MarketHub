import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { fmtNum, fmtVol, tone } from "@/utils/format";
import { quoteKey, type MarketQuote } from "@/features/dashboard/types";
import type { WatchlistItem } from "../types";
import { chartHref } from "@/features/fno/chartNav";

interface Props {
  items: WatchlistItem[];
  quotes: MarketQuote[];
  onRemove: (itemId: number) => void;
}

export function WatchlistTable({ items, quotes, onRemove }: Props) {
  const [query, setQuery] = useState("");

  // O(1) quote join. Exact exchange:token match first, bare-token fallback
  // for items whose exchange is unknown — built once per quote flush.
  const { byKey, byToken } = useMemo(() => {
    const byKey = new Map<string, MarketQuote>();
    const byToken = new Map<string, MarketQuote>();
    for (const q of quotes) {
      byKey.set(quoteKey(q), q);
      if (!byToken.has(q.instrument_token)) byToken.set(q.instrument_token, q);
    }
    return { byKey, byToken };
  }, [quotes]);

  const quoteFor = (it: WatchlistItem): MarketQuote | undefined => {
    if (it.exchange) {
      const exact = byKey.get(`${it.exchange}:${it.instrument_token}`);
      if (exact) return exact;
    }
    return byToken.get(it.instrument_token);
  };

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
              No items yet. Search above to add instruments to this watchlist.
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
              const q = quoteFor(it);
              const t = tone(q?.change);
              const sym = it.tradingsymbol ?? it.instrument_token;
              return (
                <tr key={it.id}>
                  <td>
                    <Link
                      className="link"
                      to={chartHref(
                        it.instrument_token,
                        sym,
                        it.exchange ?? "",
                        "EQUITY",
                      )}
                      title={`Chart ${sym}`}
                    >
                      {sym}
                    </Link>
                  </td>
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
