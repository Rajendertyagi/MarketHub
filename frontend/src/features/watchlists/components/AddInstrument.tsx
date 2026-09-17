import { useEffect, useRef, useState } from "react";
import { searchInstruments } from "@/api/market";
import type { Instrument } from "@/types";
import { ApiError } from "@/types";
import { Button, Input } from "@/components/ui";
import { useWatchlistMutations } from "../useWatchlists";

interface Props {
  watchlistId: number | null;
  existingTokens: Set<string>;
  onAdded?: () => void;
}

// Search-and-add box for a watchlist. Search resolves the canonical
// instrument identity via /instruments/search (never fabricated client-side);
// Add persists through POST /watchlists/{id}/items. A 409 from the backend
// ("already in watchlist") and client-side duplicates both surface as a
// friendly note instead of an error.
export function AddInstrument({ watchlistId, existingTokens, onAdded }: Props) {
  const mutations = useWatchlistMutations();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Instrument[]>([]);
  const [selected, setSelected] = useState<Instrument | null>(null);
  const [listOpen, setListOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [note, setNote] = useState<{ text: string; kind: "ok" | "err" }>({
    text: "",
    kind: "ok",
  });
  const boxRef = useRef<HTMLDivElement | null>(null);

  // Debounced catalog search with abort — same contract as Charts.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults([]);
      return;
    }
    const ctrl = new AbortController();
    const t = setTimeout(async () => {
      try {
        setResults(await searchInstruments({ q, limit: 10 }, ctrl.signal));
      } catch {
        if (!ctrl.signal.aborted) setResults([]);
      }
    }, 300);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [query]);

  useEffect(() => {
    function onDocMouseDown(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setListOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, []);

  const pick = (inst: Instrument) => {
    setSelected(inst);
    setQuery(inst.tradingsymbol);
    setResults([]);
    setListOpen(false);
    setNote({ text: "", kind: "ok" });
  };

  const add = async () => {
    if (watchlistId == null || !selected || pending) return;
    if (existingTokens.has(selected.instrument_token)) {
      setNote({ text: "Already in this watchlist.", kind: "ok" });
      return;
    }
    setPending(true);
    setNote({ text: "", kind: "ok" });
    try {
      await mutations.addItem({
        watchlistId,
        exchange: selected.exchange,
        instrument_token: selected.instrument_token,
        tradingsymbol: selected.tradingsymbol,
      });
      setSelected(null);
      setQuery("");
      setNote({ text: `Added ${selected.tradingsymbol}.`, kind: "ok" });
      onAdded?.();
    } catch (e) {
      const dup =
        e instanceof ApiError && e.status === 409
          ? "Already in this watchlist."
          : e instanceof Error
            ? e.message
            : "Could not add instrument.";
      setNote({
        text: dup,
        kind: e instanceof ApiError && e.status === 409 ? "ok" : "err",
      });
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="watchlist-add">
      <div className="watchlist-add-row">
        <div className="watchlist-add-search" ref={boxRef}>
          <Input
            className="filter-input"
            placeholder="Search catalog to add (RELIANCE, NIFTY)…"
            aria-label="Search instruments to add to watchlist"
            aria-expanded={listOpen}
            role="combobox"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelected(null);
              setListOpen(true);
            }}
            onFocus={() => setListOpen(true)}
          />
          {listOpen && results.length > 0 && (
            <ul className="watchlist-add-list" role="listbox">
              {results.map((r) => (
                <li
                  key={`${r.exchange}:${r.instrument_token}`}
                  role="option"
                  aria-selected={false}
                  className="watchlist-add-opt"
                  onMouseDown={(e) => {
                    e.preventDefault();
                    pick(r);
                  }}
                >
                  <span className="watchlist-add-opt-name">{r.tradingsymbol}</span>
                  <span className="muted">
                    {r.exchange} · {r.instrument_type}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <Button
          variant="primary"
          onClick={add}
          disabled={watchlistId == null || !selected || pending}
          title={selected ? `Add ${selected.tradingsymbol}` : "Search and pick an instrument first"}
        >
          {pending ? "Adding…" : "Add"}
        </Button>
      </div>
      {note.text && <div className={`hint ${note.kind}`}>{note.text}</div>}
    </div>
  );
}
