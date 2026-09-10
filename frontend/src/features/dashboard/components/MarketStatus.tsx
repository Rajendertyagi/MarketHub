import { inferredMarketOpen } from "../format";

// Inferred market-session banner. Clearly labelled as inferred from IST
// wall-clock time — NOT broker-confirmed.
export function MarketStatus() {
  const open = inferredMarketOpen();
  return (
    <p className="hint" data-testid="market-status-hint">
      Market status (inferred from IST time, not broker-confirmed):{" "}
      <strong>{open ? "Open" : "Closed"}</strong>
    </p>
  );
}
