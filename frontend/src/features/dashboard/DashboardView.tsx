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

function DeferredSecondary({ children }: { children: ReactNode }) {
  const [ref, visible] = useInViewport<HTMLDivElement>({ rootMargin: "200px 0px" });
  return <div ref={ref}>{visible ? children : null}</div>;
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
