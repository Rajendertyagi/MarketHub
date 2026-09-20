// Exact instrument-identity navigation to Charts.
//
// Preserves the canonical identity (instrument_key + exchange + tradingsymbol +
// instrument type) so Charts never guesses or substitutes one instrument class
// for another. Mirrors the legacy openChart behavior but passes the resolved
// identity directly via URL params (no symbol re-resolution needed).

import type { InstrumentType } from "@/types";

export function chartHref(key: string, sym: string, ex: string, type: InstrumentType): string {
  const p = new URLSearchParams({ key, sym, ex, type });
  return `/charts?${p.toString()}`;
}
