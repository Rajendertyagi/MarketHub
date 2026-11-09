// Shared canonical instrument picker.
//
// One search implementation for every place a symbol is chosen. It consumes the
// ranked canonical search (GET /api/market/search, Phase 0), the paginated
// canonical browse (GET /api/instruments/browse, Phase 1) and the backend's
// F&O read models — never a client-side guess, a fabricated token, or one
// instrument class substituted for another.
//
// `browse` opts into the browse-first surface (categories + server-side
// filters + cursor pagination). Hosts that omit it keep the original popover
// search behavior unchanged.
//
// Contract trap: the compact result's `instrument_key` is the composite
// EXCHANGE:TOKEN display key. Hosts must use `provider_token` for the bars API
// and `/chart?key=`; this component only surfaces exact canonical rows.

import { useEffect, useId, useRef, useState } from "react";
import {
  browseInstruments,
  getOptionChainView,
  resolveMarketInstrument,
  searchMarketInstruments,
} from "@/api/market";
import { Button, Input, Select, Tabs } from "@/components/ui";
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

const BROWSE_CATEGORIES = [
  { value: "all", label: "All" },
  { value: "INDEX", label: "Indices" },
  { value: "EQUITY", label: "Equities" },
  { value: "ETF", label: "ETFs" },
  { value: "fno", label: "F&O" },
] as const;

type BrowseCategory = (typeof BROWSE_CATEGORIES)[number]["value"];

// Options are never browsed unscoped (the backend rejects that): the F&O
// category browses underlyings first, then the bounded chain view.
const CATEGORY_TYPES: Record<Exclude<BrowseCategory, "fno">, string[]> = {
  all: ["INDEX", "EQUITY", "ETF", "FUTURE"],
  INDEX: ["INDEX"],
  EQUITY: ["EQUITY"],
  ETF: ["ETF"],
};

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
  /** Browse-first surface: categories, immediate rows, cursor pagination. */
  browse?: boolean;
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

function rowKey(row: MarketInstrument): string {
  return `${row.provider}:${row.provider_token}`;
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
  browse = false,
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
  const [searchEpoch, setSearchEpoch] = useState(0);

  const [category, setCategory] = useState<BrowseCategory>("all");
  const [browseRows, setBrowseRows] = useState<MarketInstrument[]>([]);
  const [browseStatus, setBrowseStatus] = useState<Status>("idle");
  const [browseError, setBrowseError] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [browseEpoch, setBrowseEpoch] = useState(0);
  const browseAbortRef = useRef<AbortController | null>(null);

  const [underlying, setUnderlying] = useState<MarketInstrument | null>(null);
  const [expiry, setExpiry] = useState("");
  const [chain, setChain] = useState<OptionChainView | null>(null);
  const [chainStatus, setChainStatus] = useState<Status>("idle");
  const [chainError, setChainError] = useState("");
  const [legPending, setLegPending] = useState(false);

  const q = value.trim();
  const scopeKey = types?.join(",") ?? "";
  const browseTypes = category === "fno" ? UNDERLYING_TYPES : CATEGORY_TYPES[category];
  const browseScopeKey = browseTypes.join(",");
  const chainVisible =
    mode === "fno" && underlying !== null && q !== "" && sameSymbol(q, underlying.symbol);
  const querySettled = mode !== "fno" || !underlying || !sameSymbol(q, underlying.symbol);
  const browseListVisible = browse && !q && !chainVisible;
  const shownRows =
    status === "loading"
      ? []
      : q || chainVisible
        ? results
        : browse
          ? browseStatus === "ready"
            ? browseRows
            : []
          : hot;

  // Debounced ranked search with abort. Only one request per settled keystroke.
  // biome-ignore lint/correctness/useExhaustiveDependencies: searchEpoch is the explicit retry trigger for a failed search.
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
    // In browse mode, a typed query searches the complete supported instrument
    // universe (category browsing is a navigation filter, not a search scope).
    // Explicit user-selected filters (exchange) still apply. Category browsing
    // without a query remains category-scoped via the browse effect.
    const scope =
      mode === "fno"
        ? UNDERLYING_TYPES
        : browse
          ? undefined
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
  }, [q, mode, scopeKey, browse, typeFilter, exchangeFilter, limit, querySettled, searchEpoch]);

  // Browse-first first page. Re-runs (and resets the cursor) whenever the
  // category, exchange filter or query changes; clearing the query returns to
  // the category's first page. Aborted requests can never land stale rows.
  // biome-ignore lint/correctness/useExhaustiveDependencies: browseEpoch is the explicit retry trigger for a failed first page.
  useEffect(() => {
    if (!browse || q) return;
    const ctrl = new AbortController();
    browseAbortRef.current?.abort();
    browseAbortRef.current = ctrl;
    setBrowseStatus("loading");
    setBrowseError("");
    setLoadingMore(false);
    browseInstruments(
      { types: browseScopeKey.split(","), exchange: exchangeFilter || undefined, limit },
      ctrl.signal,
    )
      .then((page) => {
        if (ctrl.signal.aborted) return;
        setBrowseRows(page.results);
        setCursor(page.next_cursor);
        setTotal(page.total);
        setBrowseStatus("ready");
        setActive(-1);
      })
      .catch((err) => {
        if (ctrl.signal.aborted || isAbort(err)) return;
        setBrowseRows([]);
        setCursor(null);
        setTotal(null);
        setBrowseStatus("error");
        setBrowseError(errorText(err));
      });
    return () => ctrl.abort();
  }, [browse, browseScopeKey, exchangeFilter, q, limit, browseEpoch]);

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
    if ((!open && !browse) || active < 0) return;
    document.getElementById(`${uid}-opt-${active}`)?.scrollIntoView({ block: "nearest" });
  }, [open, browse, active, uid]);

  // Closing by picking must hand focus back to the search field: the results
  // are buttons, so the click lands focus inside the popover, which unmounts.
  // The input's focus handler would reopen the popover, so the programmatic
  // focus is flagged to be ignored once.
  const suppressOpenRef = useRef(false);
  const focusInput = () => {
    const input = rootRef.current?.querySelector<HTMLInputElement>(".instrument-picker-input");
    if (!input || document.activeElement === input) return;
    suppressOpenRef.current = true;
    input.focus();
  };

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
    if (browse) {
      // The browse list stays put; the host commits the exact identity.
      onSelect(row);
      return;
    }
    setOpen(false);
    setResults([]);
    onChange(row.symbol);
    onSelect(row);
    focusInput();
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
      if (browse) {
        // Keep the chain open so another strike/leg can be picked.
        onSelect(row);
      } else {
        setOpen(false);
        onChange(row.symbol);
        onSelect(row);
        focusInput();
      }
    } catch (err) {
      setChainError(errorText(err));
    } finally {
      setLegPending(false);
    }
  };

  const loadMore = () => {
    if (!cursor || loadingMore || browseStatus !== "ready") return;
    const ctrl = new AbortController();
    browseAbortRef.current?.abort();
    browseAbortRef.current = ctrl;
    setLoadingMore(true);
    setBrowseError("");
    browseInstruments(
      { types: browseTypes, exchange: exchangeFilter || undefined, cursor, limit },
      ctrl.signal,
    )
      .then((page) => {
        if (ctrl.signal.aborted) return;
        setBrowseRows((prev) => {
          const seen = new Set(prev.map(rowKey));
          return [...prev, ...page.results.filter((r) => !seen.has(rowKey(r)))];
        });
        setCursor(page.next_cursor);
      })
      .catch((err) => {
        if (ctrl.signal.aborted || isAbort(err)) return;
        // Keep the loaded rows; the note offers to retry the same cursor.
        setBrowseError(errorText(err));
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoadingMore(false);
      });
  };

  const changeCategory = (next: BrowseCategory) => {
    if (next === category) return;
    setCategory(next);
    setMode(next === "fno" ? "fno" : "search");
    setUnderlying(null);
    setExpiry("");
    setChain(null);
    setChainError("");
    setResults([]);
    setStatus("idle");
    setError("");
    setActive(-1);
    onChange("");
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

  const chainSurface = chainVisible && underlying && (
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
            if (browse) onChange("");
          }}
        >
          Change
        </button>
      </div>

      {chainStatus === "loading" && (
        <div className="instrument-picker-note muted">Loading option chain…</div>
      )}
      {chainStatus === "error" && <div className="instrument-picker-note err">{chainError}</div>}

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
                  className={r.atm ? "instrument-picker-strike atm" : "instrument-picker-strike"}
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
                        <span className="instrument-picker-leg-ltp muted">{leg.quote.ltp}</span>
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
  );

  const listSurface = (
    <>
      {filters && mode === "search" && (
        <div className="instrument-picker-filters">
          {!types?.length && !browse && (
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
          <div key={rowKey(row)} role="none">
            <button
              id={`${uid}-opt-${i}`}
              type="button"
              role="option"
              aria-selected={i === active}
              className={i === active ? "instrument-picker-opt active" : "instrument-picker-opt"}
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
        {status === "loading" && <div className="instrument-picker-note muted">Searching…</div>}
        {browseListVisible && browseStatus === "loading" && (
          <div className="instrument-picker-note muted">Loading instruments…</div>
        )}
        {status === "error" && (
          <div className="instrument-picker-note err">
            {error}
            {browse && <Button onClick={() => setSearchEpoch((n) => n + 1)}>Retry</Button>}
          </div>
        )}
        {browseListVisible && browseStatus === "error" && (
          <div className="instrument-picker-note err">
            {browseError}
            <Button onClick={() => setBrowseEpoch((n) => n + 1)}>Retry</Button>
          </div>
        )}
        {(status === "ready" || (browseListVisible && browseStatus === "ready")) &&
          shownRows.length === 0 && (
            <div className="instrument-picker-note muted">
              {mode === "fno"
                ? "No matching underlyings."
                : browseListVisible
                  ? "No instruments in this category."
                  : "No matching instruments."}
            </div>
          )}
      </div>

      {browseListVisible && browseStatus === "ready" && browseError && (
        <div className="instrument-picker-note err">
          {browseError}
          <Button onClick={loadMore}>Retry</Button>
        </div>
      )}
      {browseListVisible && browseStatus === "ready" && (
        <div className="instrument-picker-browse-foot">
          <span className="muted">{total ?? browseRows.length} instruments</span>
          {cursor && (
            <Button onClick={loadMore} disabled={loadingMore}>
              {loadingMore ? "Loading…" : "Load more"}
            </Button>
          )}
        </div>
      )}
    </>
  );

  const surface = chainVisible && underlying ? chainSurface : listSurface;

  return (
    <div
      ref={rootRef}
      className={className ? `instrument-picker ${className}` : "instrument-picker"}
    >
      {browse && (
        <Tabs
          tabs={BROWSE_CATEGORIES.map((c) => ({ value: c.value, label: c.label }))}
          active={category}
          onChange={changeCategory}
        />
      )}
      <div className="instrument-picker-field">
        <Input
          className="instrument-picker-input"
          role="combobox"
          aria-label={ariaLabel}
          aria-autocomplete="list"
          aria-expanded={browse || open}
          aria-controls={`${uid}-list`}
          aria-activedescendant={
            (browse || open) && active >= 0 ? `${uid}-opt-${active}` : undefined
          }
          placeholder={placeholder}
          autoFocus={autoFocus}
          value={value}
          onChange={(e) => {
            onChange(e.target.value);
            setOpen(true);
            setActive(-1);
          }}
          onFocus={() => {
            if (suppressOpenRef.current) {
              suppressOpenRef.current = false;
              return;
            }
            setOpen(true);
          }}
          onKeyDown={onKeyDown}
        />
        {fno && !browse && (
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

      {browse ? (
        <div className="instrument-picker-browse">{surface}</div>
      ) : (
        open && <div className="instrument-picker-popover">{surface}</div>
      )}
    </div>
  );
}
