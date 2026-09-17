import { useMarketQuotes } from "./useMarketQuotes";
import { StreamStatus } from "@/components/StreamStatus";
import { TickerStrip } from "./components/TickerStrip";
import { IndicesWidget } from "./components/IndicesWidget";
import { SectorsWidget } from "./components/SectorsWidget";
import { FnoWidget } from "./components/FnoWidget";
import { BreadthWidget } from "./components/BreadthWidget";
import { MoversWidget } from "./components/MoversWidget";

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
