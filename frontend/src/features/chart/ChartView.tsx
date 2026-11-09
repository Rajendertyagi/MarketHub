// The `/chart` page: one Vela workspace, with symbol selection owned by the
// chart's own topbar (see `chartSymbolPicker`).
//
// The URL is the single source of truth for the charted identity
// (`key`, `sym`, `ex`, `type`, `prov`), so scanner/market-map links open the
// exact instrument and a reload restores the same chart. A symbol-only link is
// resolved through the canonical exact resolver and must match exactly — the
// chart never substitutes one instrument class for another.

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { resolveMarketInstrument, toInstrument } from "@/api/market";
import { useTheme } from "@/app/ThemeProvider";
import { Button } from "@/components/ui";
import type { Instrument, InstrumentType } from "@/types";
import { VelaChart } from "./VelaChart";

const INSTRUMENT_TYPES: InstrumentType[] = [
  "EQUITY",
  "INDEX",
  "ETF",
  "FUTURE",
  "OPTION",
  "CURRENCY",
  "COMMODITY",
];

function isInstrumentType(value: string): value is InstrumentType {
  return (INSTRUMENT_TYPES as string[]).includes(value);
}

function sameInstrument(a: Instrument | null, b: Instrument | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return (
    a.instrument_token === b.instrument_token &&
    a.exchange === b.exchange &&
    a.tradingsymbol === b.tradingsymbol &&
    a.instrument_type === b.instrument_type
  );
}

function errorText(err: unknown): string {
  return err instanceof Error && err.message ? err.message : "Request failed.";
}

function EmptyChart({ resolving }: { resolving: boolean }) {
  if (resolving) {
    return (
      <div className="state chart-empty">
        <div className="spinner" />
        <span>Resolving instrument…</span>
      </div>
    );
  }

  return (
    <div className="state chart-empty">
      <h3>Select an instrument</h3>
      <p className="hint">
        Open the catalog to choose an instrument, or open a chart from Scanners, Market Map or the Futures Board.
      </p>
      <div className="chart-empty-picker">
        <Link to="/catalog">
          <Button>Open Catalog</Button>
        </Link>
      </div>
    </div>
  );
}

export function ChartView() {
  const [params, setParams] = useSearchParams();
  const { theme } = useTheme();

  const [instrument, setInstrument] = useState<Instrument | null>(null);
  const [resolving, setResolving] = useState(false);
  const [hydrateError, setHydrateError] = useState<string | null>(null);
  const [chartError, setChartError] = useState<string | null>(null);
  // Bumped to remount the chart after a failure the user asks to retry.
  const [mountKey, setMountKey] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  const urlKey = params.get("key") ?? "";
  const urlSym = params.get("sym") ?? "";
  const urlEx = params.get("ex") ?? "";
  const urlProv = params.get("prov") ?? "";
  const rawType = params.get("type") ?? "";
  const urlType = isInstrumentType(rawType) ? rawType : "";

  // Keep the object identity stable when the URL re-states the same identity,
  // so selecting an instrument never remounts the chart it just opened.
  const apply = useCallback((next: Instrument | null) => {
    setInstrument((prev) => (sameInstrument(prev, next) ? prev : next));
    if (next) setChartError(null);
  }, []);

  // Deep links carry the exact identity; symbol-only links resolve through the
  // canonical exact resolver and must match exactly.
  const hydrate = useCallback(() => {
    abortRef.current?.abort();
    if (!urlSym) {
      apply(null);
      setResolving(false);
      setHydrateError(null);
      return;
    }
    if (urlKey && urlEx && urlType) {
      apply({
        instrument_token: urlKey,
        exchange: urlEx,
        tradingsymbol: urlSym,
        instrument_type: urlType,
        ...(urlProv ? { provider: urlProv } : {}),
      });
      setResolving(false);
      setHydrateError(null);
      return;
    }
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setResolving(true);
    setHydrateError(null);
    resolveMarketInstrument(
      urlSym,
      { exchange: urlEx || undefined, type: urlType || undefined },
      ctrl.signal,
    )
      .then((found) => {
        if (ctrl.signal.aborted) return;
        if (!found) {
          setHydrateError(`No instrument matches “${urlSym}”.`);
          return;
        }
        apply(toInstrument(found));
      })
      .catch((err) => {
        if (!ctrl.signal.aborted) setHydrateError(errorText(err));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setResolving(false);
      });
  }, [urlKey, urlSym, urlEx, urlProv, urlType, apply]);

  useEffect(() => {
    hydrate();
    return () => abortRef.current?.abort();
  }, [hydrate]);

  const selectInstrument = (next: Instrument) => {
    setHydrateError(null);
    apply(next);
    const identity: Record<string, string> = {
      key: next.instrument_token,
      sym: next.tradingsymbol,
      ex: next.exchange,
      type: next.instrument_type,
    };
    if (next.provider) identity.prov = next.provider;
    setParams(identity);
  };

  const retry = () => {
    setChartError(null);
    setHydrateError(null);
    setMountKey((k) => k + 1);
    if (!instrument) hydrate();
  };

  const error = chartError ?? hydrateError;

  return (
    <div className="chart-view">
      {error && (
        <div className="chart-banner" role="alert">
          <span className="hint err">{error}</span>
          <Button onClick={retry}>Retry</Button>
        </div>
      )}

      <div className="chart-surface">
        {instrument ? (
          <VelaChart
            key={`${instrument.instrument_token}:${mountKey}`}
            instrument={instrument}
            theme={theme}
            onSelect={selectInstrument}
            onError={setChartError}
          />
        ) : (
          <EmptyChart resolving={resolving} />
        )}
      </div>
    </div>
  );
}
