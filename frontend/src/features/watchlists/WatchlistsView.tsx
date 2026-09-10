import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/types";
import { AsyncStateView } from "@/components/ui";
import { useMarketQuotes } from "@/features/dashboard/useMarketQuotes";
import { useWatchlists, useWatchlistMutations } from "./useWatchlists";
import { exportWatchlists, importWatchlists } from "./api";
import { WatchlistPicker } from "./components/WatchlistPicker";
import { WatchlistTable } from "./components/WatchlistTable";

export function WatchlistsView() {
  const { data, status, error, refetch } = useWatchlists();
  const mutations = useWatchlistMutations();
  const { quotes } = useMarketQuotes();
  const qc = useQueryClient();

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [name, setName] = useState("");

  const watchlists = data?.watchlists ?? [];
  const selected = useMemo(
    () => watchlists.find((w) => w.id === selectedId) ?? watchlists[0],
    [watchlists, selectedId],
  );

  useEffect(() => {
    if (!selectedId && watchlists.length) setSelectedId(watchlists[0]?.id ?? null);
  }, [watchlists, selectedId]);

  const handleCreate = async () => {
    const n = name.trim();
    if (!n) return;
    await mutations.create(n);
    setName("");
  };
  const handleRename = async () => {
    const n = name.trim();
    if (!n || !selected) return;
    await mutations.rename({ id: selected.id, name: n });
    setName("");
  };
  const handleDelete = async () => {
    if (!selected) return;
    if (!window.confirm("Delete this watchlist and its items?")) return;
    await mutations.remove(selected.id);
    setSelectedId(null);
  };
  const handleExport = async () => {
    const blob = await exportWatchlists();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "watchlists.json";
    a.click();
    URL.revokeObjectURL(url);
  };
  const handleImport = async (file: File) => {
    await importWatchlists(file);
    qc.invalidateQueries({ queryKey: ["watchlists"] });
  };

  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">Watchlists</h1>
      </div>

      <WatchlistPicker
        watchlists={watchlists}
        selectedId={selected?.id ?? null}
        onSelect={setSelectedId}
        name={name}
        onNameChange={setName}
        onCreate={handleCreate}
        onRename={handleRename}
        onDelete={handleDelete}
        onExport={handleExport}
        onImport={handleImport}
      />

      {status === "pending" ? (
        <AsyncStateView status="loading" loadingLabel="Loading watchlists…" />
      ) : status === "error" ? (
        <AsyncStateView
          status="error"
          error={error as ApiError}
          onRetry={() => refetch()}
        />
      ) : (
        <div className="panel">
          <div className="panel-header">
            <h2>{selected?.name ?? "Watchlist"}</h2>
          </div>
          <WatchlistTable
            items={selected?.items ?? []}
            quotes={quotes}
            onRemove={(itemId) => mutations.removeItem(itemId)}
          />
        </div>
      )}
    </div>
  );
}
