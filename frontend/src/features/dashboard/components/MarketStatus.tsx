import { inferredMarketOpen } from "@/utils/market";

// Inferred market-session chip. Clearly labelled as inferred from IST
// wall-clock time — NOT broker-confirmed.
export function MarketStatus() {
  const open = inferredMarketOpen();
  return (
    <span
      className={`market-status ${open ? "is-open" : "is-closed"}`}
      data-testid="market-status-hint"
      title="Inferred from IST time, not broker-confirmed"
    >
      <span className="market-status-dot" aria-hidden="true" />
      <span className="market-status-label">Market {open ? "Open" : "Closed"}</span>
      <span className="market-status-note muted">inferred</span>
    </span>
  );
}
