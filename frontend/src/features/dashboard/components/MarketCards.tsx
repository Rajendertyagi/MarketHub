import { fmtNum, tone } from "@/utils/format";
import type { MarketQuote } from "../types";

interface Props {
  quotes: MarketQuote[];
}

export function MarketCards({ quotes }: Props) {
  if (!quotes.length) {
    return <div className="market-cards empty-row">No instruments subscribed</div>;
  }
  return (
    <div className="market-cards">
      {quotes.map((q) => {
        const t = tone(q.change);
        return (
          <div
            key={`${q.exchange}:${q.instrument_token}`}
            className={`mcard ${t}`}
          >
            <div className="mcard-sym">{q.tradingsymbol ?? q.instrument_token}</div>
            <div className="mcard-ltp">{fmtNum(q.ltp)}</div>
            <div className="mcard-chg">
              {q.change != null ? `${q.change > 0 ? "+" : ""}${fmtNum(q.change)}` : "—"}
              {q.change_percent != null ? ` (${fmtNum(q.change_percent)}%)` : ""}
            </div>
          </div>
        );
      })}
    </div>
  );
}
