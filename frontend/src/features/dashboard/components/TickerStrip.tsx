import { fmtNum, fmtVol, tone } from "@/utils/format";
import { isStaleQuote } from "../format";
import type { MarketQuote } from "../types";

interface Props {
  quotes: MarketQuote[];
}

export function TickerStrip({ quotes }: Props) {
  if (!quotes.length) {
    return <div className="app-ticker ticker-empty">No market data yet</div>;
  }
  return (
    <div className="app-ticker">
      {quotes.map((q) => {
        const t = tone(q.change);
        const stale = isStaleQuote(q);
        return (
          <span
            key={`${q.exchange}:${q.instrument_token}`}
            className={`ticker-item${stale ? " is-stale" : ""}`}
            title={stale ? "Stale — no tick within the last 5 minutes" : undefined}
          >
            <span className="ticker-sym">{q.tradingsymbol ?? q.instrument_token}</span>
            <span className="ticker-ltp">{fmtNum(q.ltp)}</span>
            <span className={`ticker-chg ${t}`}>
              {q.change != null ? `${q.change > 0 ? "+" : ""}${fmtNum(q.change)}` : "—"}
              {q.change_percent != null ? ` (${fmtNum(q.change_percent)}%)` : ""}
            </span>
            <span className="ticker-vol">{fmtVol(q.volume)}</span>
            {stale && <span className="stale-tag">stale</span>}
          </span>
        );
      })}
    </div>
  );
}
