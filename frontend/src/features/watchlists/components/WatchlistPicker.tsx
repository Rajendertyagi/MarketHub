import type { Watchlist } from "../types";

interface Props {
  watchlists: Watchlist[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  name: string;
  onNameChange: (v: string) => void;
  onCreate: () => void;
  onRename: () => void;
  onDelete: () => void;
  onExport: () => void;
  onImport: (file: File) => void;
}

export function WatchlistPicker({
  watchlists,
  selectedId,
  onSelect,
  name,
  onNameChange,
  onCreate,
  onRename,
  onDelete,
  onExport,
  onImport,
}: Props) {
  return (
    <div className="toolbar watchlist-picker">
      <select
        className="ui-select"
        aria-label="Select watchlist"
        value={selectedId ?? ""}
        onChange={(e) => onSelect(Number(e.target.value))}
      >
        {watchlists.map((w) => (
          <option key={w.id} value={w.id}>
            {w.name}
          </option>
        ))}
      </select>
      <input
        type="text"
        className="filter-input"
        placeholder="New watchlist name"
        aria-label="Watchlist name"
        value={name}
        onChange={(e) => onNameChange(e.target.value)}
      />
      <button type="button" className="btn" onClick={onCreate}>
        Create
      </button>
      <button type="button" className="btn" onClick={onRename}>
        Rename
      </button>
      <button type="button" className="btn btn-outline-danger" onClick={onDelete}>
        Delete
      </button>
      <button type="button" className="btn" onClick={onExport}>
        Export
      </button>
      <label className="btn">
        Import
        <input
          type="file"
          accept="application/json"
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onImport(f);
          }}
        />
      </label>
    </div>
  );
}
