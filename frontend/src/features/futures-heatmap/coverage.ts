// Coverage / OI-basis vocabulary — mirrors the backend exactly
// (market.coverage_states, market.oi). Broker-neutral: the UI never invents
// states and never constructs provider keys.

export const COVERAGE_LIVE = "live";
export const COVERAGE_STALE = "stale";
export const COVERAGE_NO_DATA = "no_data";
export const COVERAGE_UNSUBSCRIBED = "unsubscribed";
export const COVERAGE_UNCOVERED = "uncovered";
export const COVERAGE_CAPACITY_LIMITED = "capacity_limited";
export const COVERAGE_PROVIDER_UNAVAILABLE = "provider_unavailable";

const LABELS: Record<string, string> = {
  live: "Live",
  stale: "Stale",
  no_data: "No data",
  unsubscribed: "Not subscribed",
  uncovered: "Uncovered",
  capacity_limited: "Capacity limited",
  provider_unavailable: "Provider unavailable",
};

export function coverageLabel(state: string | null | undefined): string {
  if (!state) return "—";
  return LABELS[state] ?? state;
}

export function coverageClass(state: string | null | undefined): string {
  return `cov cov-${(state ?? "unknown").replace(/_/g, "-")}`;
}

// A tile is priced only when it carries a live/stale quote. `no_data` is NOT
// "zero" and NOT "unavailable instrument" — it simply has no quote right now.
export function isPriced(state: string | null | undefined): boolean {
  return state === COVERAGE_LIVE || state === COVERAGE_STALE;
}

const OI_BASIS_LABELS: Record<string, string> = {
  provider: "Provider",
  persisted: "Persisted",
  unavailable: "No baseline",
};

export function oiBasisLabel(basis: string | null | undefined): string {
  if (!basis) return "—";
  return OI_BASIS_LABELS[basis] ?? basis;
}
