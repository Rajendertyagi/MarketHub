import type { ReactNode } from "react";
import { StreamStatus } from "@/components/StreamStatus";
import { BreadthWidget } from "./components/BreadthWidget";
import { FnoWidget } from "./components/FnoWidget";
import { IndicesWidget } from "./components/IndicesWidget";
import { MoversWidget } from "./components/MoversWidget";
import { SectorsWidget } from "./components/SectorsWidget";
import { TickerStrip } from "./components/TickerStrip";
import { useInViewport } from "./useInViewport";
import { useMarketQuotes } from "./useMarketQuotes";

const SECONDARY_IO_OPTIONS: IntersectionObserverInit = { rootMargin: "200px 0px" };

function DeferredSecondary({ children }: { children: ReactNode }) {
  const [ref, visible] = useInViewport<HTMLDivElement>(SECONDARY_IO_OPTIONS);
  return (
    <div ref={ref} className="dash-secondary-slot">
      {visible ? children : <div className="dash-secondary-placeholder" aria-hidden="true" />}
    </div>
  );
}

export function DashboardView() {
  const { quotes, byCanonical, connected } = useMarketQuotes();

  return (
    <div className="dash">
      <div className="dash-status">
        <StreamStatus connected={connected} />
      </div>
      <TickerStrip quotes={quotes} />

      <section className="dash-hero" aria-label="Major indices">
        <IndicesWidget quotes={quotes} byCanonical={byCanonical} />
      </section>

      <section className="dash-secondary" aria-label="Market detail">
        <DeferredSecondary>
          <SectorsWidget />
        </DeferredSecondary>
        <DeferredSecondary>
          <BreadthWidget />
        </DeferredSecondary>
        <DeferredSecondary>
          <FnoWidget />
        </DeferredSecondary>
        <DeferredSecondary>
          <MoversWidget />
        </DeferredSecondary>
      </section>
    </div>
  );
}
