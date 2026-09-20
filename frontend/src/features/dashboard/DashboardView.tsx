import { StreamStatus } from "@/components/StreamStatus";
import { BreadthWidget } from "./components/BreadthWidget";
import { FnoWidget } from "./components/FnoWidget";
import { IndicesWidget } from "./components/IndicesWidget";
import { MoversWidget } from "./components/MoversWidget";
import { SectorsWidget } from "./components/SectorsWidget";
import { TickerStrip } from "./components/TickerStrip";
import { useMarketQuotes } from "./useMarketQuotes";

export function DashboardView() {
  const { quotes, connected } = useMarketQuotes();

  return (
    <div className="dash">
      <div className="dash-status">
        <StreamStatus connected={connected} />
      </div>
      <TickerStrip quotes={quotes} />

      <section className="dash-hero" aria-label="Major indices">
        <IndicesWidget quotes={quotes} />
      </section>

      <section className="dash-secondary" aria-label="Market detail">
        <SectorsWidget />
        <BreadthWidget />
        <FnoWidget />
        <MoversWidget />
      </section>
    </div>
  );
}
