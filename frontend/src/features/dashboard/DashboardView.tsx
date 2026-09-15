import { useMarketQuotes } from "./useMarketQuotes";
import { TickerStrip } from "./components/TickerStrip";
import { IndicesWidget } from "./components/IndicesWidget";
import { SectorsWidget } from "./components/SectorsWidget";
import { FnoWidget } from "./components/FnoWidget";
import { BreadthWidget } from "./components/BreadthWidget";

export function DashboardView() {
  const { quotes } = useMarketQuotes();

  return (
    <div className="dash">
      <TickerStrip quotes={quotes} />

      <section className="dash-hero" aria-label="Major indices">
        <IndicesWidget quotes={quotes} />
      </section>

      <section className="dash-secondary" aria-label="Market detail">
        <SectorsWidget />
        <BreadthWidget />
        <FnoWidget />
      </section>
    </div>
  );
}
