// Shared canonical instrument picker.
//
// One search implementation for every place a symbol is chosen. It consumes the
// ranked canonical search (GET /api/market/search, Phase 0) and the backend's
// F&O read models — never a client-side guess, a fabricated token, or one
// instrument class substituted for another.
//
// Contract trap: the compact result's `instrument_key` is the composite
// EXCHANGE:TOKEN display key. Hosts must use `provider_token` for the bars API
// and `/chart?key=`; this component only surfaces exact canonical rows.

import { useEffect, useId, useRef, useState } from "react";
import { getOptionChainView, resolveMarketInstrument, searchMarketInstruments } from "@/api/market";
import { Input, Select } from "@/components/ui";
import type { MarketInstrument, OptionChainView, OptionLeg } from "@/types";
import { ApiError } from "@/types";

const DEBOUNCE_MS = 250;

const SEARCH_TYPES = [
  { value: "EQUITY", label: "Equity" },
  { value: "INDEX", label: "Index" },
  { value: "ETF", label: "ETF" },
  { value: "FUTURE", label: "Futures" },
  { value: "OPTION", label: "Options" },
];

const EXCHANGES = ["NSE", "BSE", "NFO", "BFO"];

// The only classes an F&O underlying can have. Derivatives attach to a cash
// equity/index, so the structured mode starts there.
const UNDERLYING_TYPES = ["EQUITY", "INDEX"];

export interface InstrumentPickerProps {
  /** Controlled query text. */
  value: string;
  onChange: (value: string) => void;
  /** Exact canonical row the user picked. */
  onSelect: (row: MarketInstrument) => void;
  placeholder?: string;
  ariaLabel?: string;
  className?: string;
  /** Fixed canonical classes; hides the type filter when set. */
  types?: string[];
  /** Show the type/exchange filters inside the result surface. */
  filters?: boolean;
  /** Enable the structured underlying → expiry → strike → CE/PE mode. */
  fno?: boolean;
  limit?: number;
  autoFocus?: boolean;
  /** Local suggestions shown before a query is typed (never server truth). */
  hot?: MarketInstrument[];
}

type PickerMode = "search" | "fno";
type Status = "idle" | "loading" | "ready" | "error";

function isAbort(err: unknown): boolean {
  return err instanceof ApiError && err.kind === "abort";
}

function errorText(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return "Search failed.";
}

function resultMeta(row: MarketInstrument): string {
  const bits = [row.exchange, row.type];
  if (row.type === "OPTION") {
    const contract = [row.expiry, row.strike, row.option_type].filter(Boolean).join(" ");
    if (contract) bits.push(contract);
  } else if (row.type === "FUTURE" && row.expiry) {
    bits.push(row.expiry);
  }
  return bits.join(" · ");
}

function sameSymbol(a: string, b: string): boolean {
  return a.trim().toUpperCase() === b.trim().toUpperCase();
}

export function InstrumentPicker({
  value,
  onChange,
  onSelect,
  placeholder = "Search symbol…",
  ariaLabel = "Search instruments",
  className,
  types,
  filters = true,
  fno = true,
  limit = 15,
  autoFocus = false,
  hot = [],
}: InstrumentPickerProps) {
  const uid = useId().replace(/:/g, "");
  const rootRef = useRef<HTMLDivElement | null>(null);

  const [mode, setMode] = useState<PickerMode>("search");
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<MarketInstrument[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState("");
  const [active, setActive] = useState(-1);
  const [typeFilter, setTypeFilter] = useState("");
  const [exchangeFilter, setExchangeFilter] = useState("");

  const [underlying, setUnderlying] = useState<MarketInstrument | null>(null);
  const [expiry, setExpiry] = useState("");
  const [chain, setChain] = useState<OptionChainView | null>(null);
  const [chainStatus, setChainStatus] = useState<Status>("idle");
  const [chainError, setChainError] = useState("");
  const [legPending, setLegPending] = useState(false);

  const q = value.trim();
  const scopeKey = types?.join(",") ?? "";
  const chainVisible =
    mode === "fno" && underlying !== null && q !== "" && sameSymbol(q, underlying.symbol);
  const querySettled = mode !== "fno" || !underlying || !sameSymbol(q, underlying.symbol);
  const shownRows = status === "loading" ? [] : q || chainVisible ? results : hot;

  // Debounced ranked search with abort. Only one request per settled keystroke.
  useEffect(() => {
    if (!querySettled) {
      setResults([]);
      setStatus("idle");
      return;
    }
    if (!q) {
      setResults([]);
      setStatus("idle");
      setError("");
      return;
    }
    const scope =
      mode === "fno"
        ? UNDERLYING_TYPES
        : scopeKey
          ? scopeKey.split(",")
          : typeFilter
            ? [typeFilter]
            : undefined;
    const exchange = mode === "fno" ? undefined : exchangeFilter || undefined;
    const ctrl = new AbortController();
    setStatus("loading");
    setError("");
    const timer = setTimeout(() => {
      searchMarketInstruments({ q, types: scope, exchange, limit }, ctrl.signal)
        .then((rows) => {
          if (ctrl.signal.aborted) return;
          setResults(rows);
          setStatus("ready");
          setActive(rows.length ? 0 : -1);
        })
        .catch((err) => {
          if (ctrl.signal.aborted || isAbort(err)) return;
          setResults([]);
          setStatus("error");
          setError(errorText(err));
          setActive(-1);
        });
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [q, mode, scopeKey, typeFilter, exchangeFilter, limit, querySettled]);

  // Structured mode: after an underlying is chosen, the chain is backend truth.
  useEffect(() => {
    if (!underlying) {
      setChain(null);
      setChainStatus("idle");
      setChainError("");
      return;
    }
    const ctrl = new AbortController();
    setChainStatus("loading");
    setChainError("");
    getOptionChainView(
      { underlying: underlying.symbol, expiry: expiry || undefined, window: 10 },
      ctrl.signal,
    )
      .then((view) => {
        if (ctrl.signal.aborted) return;
        setChain(view);
        setChainStatus("ready");
      })
      .catch((err) => {
        if (ctrl.signal.aborted || isAbort(err)) return;
        setChain(null);
        setChainStatus("error");
        setChainError(errorText(err));
      });
    return () => ctrl.abort();
  }, [underlying, expiry]);

  useEffect(() => {
    function onDocMouseDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
        setActive(-1);
      }
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, []);

  useEffect(() => {
    if (!open || active < 0) return;
    document.getElementById(`${uid}-opt-${active}`)?.scrollIntoView({ block: "nearest" });
  }, [open, active, uid]);

  const pickRow = (row: MarketInstrument) => {
    setActive(-1);
    if (mode === "fno") {
      setUnderlying(row);
      setExpiry("");
      setChain(null);
      setChainError("");
      onChange(row.symbol);
      setOpen(true);
      return;
    }
    setOpen(false);
    setResults([]);
    onChange(row.symbol);
    onSelect(row);
  };

  const pickLeg = async (leg: OptionLeg) => {
    if (legPending) return;
    setLegPending(true);
    setChainError("");
    try {
      const row = await resolveMarketInstrument(leg.symbol, {
        exchange: leg.exchange,
        type: "OPTION",
      });
      if (!row) {
        setChainError(`No canonical contract for ${leg.symbol}.`);
        return;
      }
      setOpen(false);
      onChange(row.symbol);
      onSelect(row);
    } catch (err) {
      setChainError(errorText(err));
    } finally {
      setLegPending(false);
    }
  };

  const toggleMode = () => {
    setMode((prev) => (prev === "search" ? "fno" : "search"));
    setOpen(false);
    setActive(-1);
    setResults([]);
    setStatus("idle");
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
      setActive(-1);
      return;
    }
    const rows = shownRows;
    if (!rows.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((i) => Math.min(i + 1, rows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Home") {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActive(rows.length - 1);
    } else if (e.key === "Enter") {
      const row = rows[Math.max(active, 0)];
      if (row) {
        e.preventDefault();
        pickRow(row);
      }
    }
  };

  return (
    <div
      ref={rootRef}
      className={className ? `instrument-picker ${className}` : "instrument-picker"}
    >
      <div className="instrument-picker-field">
        <Input
          className="instrument-picker-input"
          role="combobox"
          aria-label={ariaLabel}
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={`${uid}-list`}
          aria-activedescendant={open && active >= 0 ? `${uid}-opt-${active}` : undefined}
          placeholder={placeholder}
          autoFocus={autoFocus}
          value={value}
          onChange={(e) => {
            onChange(e.target.value);
            setOpen(true);
            setActive(-1);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
        />
        {fno && (
          <button
            type="button"
            className={mode === "fno" ? "instrument-picker-mode active" : "instrument-picker-mode"}
            aria-pressed={mode === "fno"}
            title="Structured F&O picker (underlying → expiry → strike → CE/PE)"
            onClick={toggleMode}
          >
            F&amp;O
          </button>
        )}
      </div>

      {open && (
        <div className="instrument-picker-popover">
          {chainVisible && underlying ? (
            <div className="instrument-picker-chain">
              <div className="instrument-picker-chain-head">
                <span className="instrument-picker-chain-symbol">{underlying.symbol}</span>
                <span className="muted instrument-picker-chain-sub">
                  {underlying.exchange} · {underlying.type}
                </span>
                <button
                  type="button"
                  className="instrument-picker-link"
                  onClick={() => {
                    setUnderlying(null);
                    setChain(null);
                    setExpiry("");
                  }}
                >
                  Change
                </button>
              </div>

              {chainStatus === "loading" && (
                <div className="instrument-picker-note muted">Loading option chain…</div>
              )}
              {chainStatus === "error" && (
                <div className="instrument-picker-note err">{chainError}</div>
              )}

              {chainStatus === "ready" && chain && (
                <>
                  <div className="instrument-picker-chain-head">
                    <Select
                      aria-label="Option expiry"
                      className="select instrument-picker-expiry"
                      value={expiry || chain.expiry}
                      onChange={(e) => setExpiry(e.target.value)}
                    >
                      {chain.expiries_available.map((x) => (
                        <option key={x} value={x}>
                          {x}
                        </option>
                      ))}
                    </Select>
                    <span className="muted instrument-picker-chain-sub">
                      {chain.strikes_loaded} strikes · ATM {chain.atm_strike ?? "—"}
                    </span>
                  </div>
                  <div className="instrument-picker-chain-rows">
                    {chain.rows.map((r) => (
                      <div key={r.strike} className="instrument-picker-chain-row">
                        <span
                          className={
                            r.atm ? "instrument-picker-strike atm" : "instrument-picker-strike"
                          }
                        >
                          {r.strike}
                        </span>
                        {(["call", "put"] as const).map((side) => {
                          const leg = r[side];
                          if (!leg) {
                            return (
                              <span key={side} className="instrument-picker-leg empty">
                                —
                              </span>
                            );
                          }
                          return (
                            <button
                              key={side}
                              type="button"
                              className="instrument-picker-leg"
                              disabled={legPending}
                              aria-label={`${leg.symbol} ${leg.option_type}`}
                              onClick={() => pickLeg(leg)}
                            >
                              <span className="instrument-picker-leg-type">{leg.option_type}</span>
                              {leg.quote?.ltp != null && (
                                <span className="instrument-picker-leg-ltp muted">
                                  {leg.quote.ltp}
                                </span>
                              )}
                            </button>
                          );
                        })}
                      </div>
                    ))}
                  </div>
                  {chainError && <div className="instrument-picker-note err">{chainError}</div>}
                </>
              )}
            </div>
          ) : (
            <>
              {filters && mode === "search" && (
                <div className="instrument-picker-filters">
                  {!types?.length && (
                    <Select
                      aria-label="Filter by instrument type"
                      className="select instrument-picker-filter"
                      value={typeFilter}
                      onChange={(e) => {
                        setTypeFilter(e.target.value);
                        setOpen(true);
                      }}
                    >
                      <option value="">Cash</option>
                      {SEARCH_TYPES.map((t) => (
                        <option key={t.value} value={t.value}>
                          {t.label}
                        </option>
                      ))}
                    </Select>
                  )}
                  <Select
                    aria-label="Filter by exchange"
                    className="select instrument-picker-filter"
                    value={exchangeFilter}
                    onChange={(e) => {
                      setExchangeFilter(e.target.value);
                      setOpen(true);
                    }}
                  >
                    <option value="">All venues</option>
                    {EXCHANGES.map((x) => (
                      <option key={x} value={x}>
                        {x}
                      </option>
                    ))}
                  </Select>
                </div>
              )}

              <div
                className="instrument-picker-list"
                id={`${uid}-list`}
                role="listbox"
                aria-label={mode === "fno" ? "F&O underlyings" : "Instrument results"}
              >
                {shownRows.map((row, i) => (
                  <div key={`${row.provider}:${row.provider_token}`} role="none">
                    <button
                      id={`${uid}-opt-${i}`}
                      type="button"
                      role="option"
                      aria-selected={i === active}
                      className={
                        i === active ? "instrument-picker-opt active" : "instrument-picker-opt"
                      }
                      title={row.canonical_id ?? row.instrument_key}
                      onMouseDown={(e) => e.preventDefault()}
                      onMouseEnter={() => setActive(i)}
                      onClick={() => pickRow(row)}
                    >
                      <span className="instrument-picker-opt-name">{row.symbol}</span>
                      <span className="instrument-picker-opt-meta muted">{resultMeta(row)}</span>
                      <span className="instrument-picker-provider">{row.provider}</span>
                    </button>
                  </div>
                ))}
                {status === "loading" && (
                  <div className="instrument-picker-note muted">Searching…</div>
                )}
                {status === "error" && <div className="instrument-picker-note err">{error}</div>}
                {status === "ready" && shownRows.length === 0 && (
                  <div className="instrument-picker-note muted">
                    {mode === "fno" ? "No matching underlyings." : "No matching instruments."}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
