// Canonical instrument catalog — the shared picker's first full-page host.
//
// The page owns no search of its own: InstrumentPicker performs the ranked,
// deduped GET /api/market/search call (and the structured F&O mode). Selecting
// a row commits that exact canonical identity here, and "Open chart" carries it
// to the chart route: the BARE `provider_token` (never the composite display
// `instrument_key`) plus the owning provider.

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { InstrumentPicker } from "@/components/InstrumentPicker";
import { Button } from "@/components/ui";
import type { MarketInstrument } from "@/types";

function chartHref(row: MarketInstrument): string {
  const p = new URLSearchParams({
    key: row.provider_token,
    sym: row.symbol,
    ex: row.exchange,
    type: row.type,
    prov: row.provider,
  });
  return `/chart?${p.toString()}`;
}

interface Field {
  label: string;
  value: (row: MarketInstrument) => string | null;
}

function optional(value: string | number | null | undefined): string | null {
  if (value === null || value === undefined || value === "") return null;
  return String(value);
}

const FIELDS: Field[] = [
  { label: "Symbol", value: (r) => r.symbol },
  { label: "Name", value: (r) => optional(r.display_name) },
  { label: "Type", value: (r) => r.type },
  { label: "Exchange", value: (r) => r.exchange },
  { label: "Provider", value: (r) => r.provider },
  { label: "Underlying", value: (r) => optional(r.underlying) },
  { label: "Expiry", value: (r) => optional(r.expiry) },
  { label: "Strike", value: (r) => optional(r.strike) },
  { label: "Option type", value: (r) => optional(r.option_type) },
  { label: "Lot size", value: (r) => optional(r.lot_size) },
  { label: "Display key", value: (r) => r.instrument_key },
];

export function CatalogView() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<MarketInstrument | null>(null);

  const rows = selected
    ? FIELDS.map((f) => ({ label: f.label, value: f.value(selected) })).filter(
        (r): r is { label: string; value: string } => r.value !== null,
      )
    : [];

  return (
    <div className="catalog-view">
      <header className="catalog-header">
        <h1>Instrument Catalog</h1>
        <p className="muted">
          Search the canonical catalog. Results are provider-deduped by the backend, so one row is
          one instrument with the provider that owns its token.
        </p>
      </header>

      <div className="catalog-search">
        <InstrumentPicker
          value={query}
          onChange={(next) => {
            setQuery(next);
            setSelected(null);
          }}
          onSelect={setSelected}
          placeholder="Search the catalog (RELIANCE, NIFTY, BANKNIFTY 25000 CE)…"
          ariaLabel="Search the instrument catalog"
          limit={50}
          autoFocus
        />
      </div>

      {selected ? (
        <section className="panel catalog-detail">
          <div className="catalog-detail-head">
            <div>
              <h2 className="catalog-symbol">{selected.display_name ?? selected.symbol}</h2>
              <span className="muted">
                {selected.exchange} · {selected.type} ·{" "}
                <span className="catalog-provider">{selected.provider}</span>
              </span>
            </div>
            <div className="catalog-actions">
              <Button variant="primary" onClick={() => navigate(chartHref(selected))}>
                Open chart
              </Button>
              <Button
                onClick={() => {
                  setSelected(null);
                  setQuery("");
                }}
              >
                Clear
              </Button>
            </div>
          </div>

          <table className="catalog-table" aria-label="Selected instrument">
            <tbody>
              {rows.map((r) => (
                <tr key={r.label}>
                  <th scope="row">{r.label}</th>
                  <td>{r.value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : (
        <div className="state catalog-empty">
          <span className="muted">
            Type a symbol to browse the canonical catalog, or pick one from the results.
          </span>
        </div>
      )}
    </div>
  );
}
