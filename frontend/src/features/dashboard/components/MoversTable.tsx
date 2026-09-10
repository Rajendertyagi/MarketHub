import { fmtNum, fmtVol, tone } from "@/utils/format";
import type { MarketQuote } from "../types";

interface Props {
  quotes: MarketQuote[];
}

interface MoverRow {
  category: string;
  quote: MarketQuote;
}

function buildMovers(quotes: MarketQuote[]): MoverRow[] {
  const withPct = quotes.filter((q) => q.change_percent != null);
  if (!withPct.length) return [];
  const byPct = [...withPct].sort(
    (a, b) => (b.change_percent ?? 0) - (a.change_percent ?? 0),
  );
  const byVol = [...withPct].sort((a, b) => (b.volume ?? 0) - (a.volume ?? 0));
  const rows: MoverRow[] = [];
  for (const q of byPct.slice(0, 2)) rows.push({ category: "Top Gainer", quote: q });
  for (const q of byPct.slice(-2).reverse()) {
    if ((q.change_percent ?? 0) < 0) rows.push({ category: "Top Loser", quote: q });
  }
  for (const q of byVol.slice(0, 2)) rows.push({ category: "Volume Leader", quote: q });
  return rows;
}

export function MoversTable({ quotes }: Props) {
  const rows = buildMovers(quotes);
  if (!rows.length) {
    return (
      <table className="data-table">
        <tbody>
          <tr>
            <td colSpan={5} className="empty-row">
              Waiting for market data
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
          <th>Category</th>
          <th>Symbol</th>
          <th>LTP</th>
          <th>Chg%</th>
          <th>Volume</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={`${r.category}-${r.quote.exchange}:${r.quote.instrument_token}`}>
            <td>{r.category}</td>
            <td>{r.quote.tradingsymbol ?? r.quote.instrument_token}</td>
            <td>{fmtNum(r.quote.ltp)}</td>
            <td className={tone(r.quote.change)}>
              {r.quote.change_percent != null ? `${fmtNum(r.quote.change_percent)}%` : "—"}
            </td>
            <td>{fmtVol(r.quote.volume)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
