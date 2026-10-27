import { useState } from "react";
import { InstrumentPicker } from "@/components/InstrumentPicker";
import { Button } from "@/components/ui";
import type { MarketInstrument } from "@/types";
import { ApiError } from "@/types";
import { useWatchlistMutations } from "../useWatchlists";

interface Props {
  watchlistId: number | null;
  existingTokens: Set<string>;
  onAdded?: () => void;
}

// Search-and-add box for a watchlist. The shared InstrumentPicker resolves the
// exact canonical identity (bare `provider_token`, never a fabricated token);
// Add persists through POST /watchlists/{id}/items. A 409 from the backend
// ("already in watchlist") and client-side duplicates both surface as a
// friendly note instead of an error.
export function AddInstrument({ watchlistId, existingTokens, onAdded }: Props) {
  const mutations = useWatchlistMutations();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<MarketInstrument | null>(null);
  const [pending, setPending] = useState(false);
  const [note, setNote] = useState<{ text: string; kind: "ok" | "err" }>({
    text: "",
    kind: "ok",
  });

  const pick = (row: MarketInstrument) => {
    setSelected(row);
    setNote({ text: "", kind: "ok" });
  };

  const add = async () => {
    if (watchlistId == null || !selected || pending) return;
    if (existingTokens.has(selected.provider_token)) {
      setNote({ text: "Already in this watchlist.", kind: "ok" });
      return;
    }
    setPending(true);
    setNote({ text: "", kind: "ok" });
    try {
      await mutations.addItem({
        watchlistId,
        exchange: selected.exchange,
        instrument_token: selected.provider_token,
        tradingsymbol: selected.symbol,
      });
      setSelected(null);
      setQuery("");
      setNote({ text: `Added ${selected.symbol}.`, kind: "ok" });
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
        <InstrumentPicker
          value={query}
          onChange={(next) => {
            setQuery(next);
            setSelected(null);
          }}
          onSelect={pick}
          placeholder="Search catalog to add (RELIANCE, NIFTY)…"
          ariaLabel="Search instruments to add to watchlist"
          limit={10}
        />
        <Button
          variant="primary"
          onClick={add}
          disabled={watchlistId == null || !selected || pending}
          title={selected ? `Add ${selected.symbol}` : "Search and pick an instrument first"}
        >
          {pending ? "Adding…" : "Add"}
        </Button>
      </div>
      {note.text && <div className={`hint ${note.kind}`}>{note.text}</div>}
    </div>
  );
}
