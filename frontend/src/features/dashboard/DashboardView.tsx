import { useMemo, useState } from "react";
import { useMarketQuotes } from "./useMarketQuotes";
import { quoteKey } from "./types";
import { TickerStrip } from "./components/TickerStrip";
import { MarketCards } from "./components/MarketCards";
import { MoversTable } from "./components/MoversTable";
import { MarketsTable } from "./components/MarketsTable";
import { MarketStatus } from "./components/MarketStatus";
import { QuoteDrawer } from "./components/QuoteDrawer";

export function DashboardView() {
  const { quotes, connected } = useMarketQuotes();
  const [term, setTerm] = useState("");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const t = term.trim().toLowerCase();
    if (!t) return quotes;
    return quotes.filter((q) =>
      (q.tradingsymbol ?? q.instrument_token).toLowerCase().includes(t),
    );
  }, [quotes, term]);

  const selectedQuote = useMemo(
    () => quotes.find((q) => quoteKey(q) === selectedKey) ?? null,
    [quotes, selectedKey],
  );

  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">Dashboard</h1>
        <span className={`chip ${connected ? "chip-on" : "chip-off"}`}>
          {connected ? "Live" : "Reconnecting"}
        </span>
      </div>

      <MarketStatus />

      <TickerStrip quotes={quotes} />

      <div className="panel">
        <div className="panel-header">
          <h2>Market Cards</h2>
        </div>
        <MarketCards quotes={quotes} />
      </div>

      <div className="panel">
        <div className="panel-header">
          <h2>Subscribed Universe Movers</h2>
        </div>
        <MoversTable quotes={quotes} />
      </div>

      <div className="panel">
        <div className="panel-header">
          <h2>Live Market</h2>
        </div>
        <div className="toolbar">
          <input
            type="text"
            className="filter-input"
            placeholder="Filter symbols…"
            aria-label="Filter market symbols"
            value={term}
            onChange={(e) => setTerm(e.target.value)}
          />
          <span className="hint">{filtered.length} instruments</span>
        </div>
        <div className="table-scroll">
          <MarketsTable quotes={filtered} onSelect={setSelectedKey} />
        </div>
      </div>

      <QuoteDrawer quote={selectedQuote} onClose={() => setSelectedKey(null)} />
    </div>
  );
}
