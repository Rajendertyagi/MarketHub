import type { NewsFilters, NewsSource } from "../types";

interface Props {
  sources: NewsSource[];
  filters: NewsFilters;
  onFilterChange: (next: NewsFilters) => void;
  onManageClick: () => void;
}

const MAX_AGE = [
  { value: "", label: "Any time" },
  { value: "1", label: "Last hour" },
  { value: "6", label: "Last 6 hours" },
  { value: "24", label: "Last 24 hours" },
  { value: "72", label: "Last 3 days" },
  { value: "168", label: "Last 7 days" },
];

// Left pane of the reader: the source list (primary filter dimension) plus a
// compact set of filters (search / symbol / age). No category tabs — sources
// are the organizing axis, exactly like a standard RSS reader.
export function NewsSourcesPane({
  sources,
  filters,
  onFilterChange,
  onManageClick,
}: Props) {
  const selectedSource = filters.source_ids?.[0] ?? "";
  const set = (patch: Partial<NewsFilters>) =>
    onFilterChange({ ...filters, ...patch });

  const toggleSource = (id: string) =>
    set({ source_ids: selectedSource === id ? undefined : [id] });

  const enabled = sources.filter((s) => s.enabled);

  return (
    <aside className="news-pane news-sources-pane">
      <div className="news-pane-header">
        <h2>Sources</h2>
        <button className="btn btn-compact" onClick={onManageClick}>
          Manage
        </button>
      </div>

      <div className="news-source-filters">
        <input
          className="input filter-input"
          type="text"
          placeholder="Search articles…"
          aria-label="Search articles"
          value={(filters.keywords_include ?? []).join(" ")}
          onChange={(e) =>
            set({
              keywords_include: e.target.value.trim()
                ? e.target.value.trim().split(/\s+/)
                : undefined,
            })
          }
        />
        <input
          className="input filter-input"
          type="text"
          placeholder="Symbol (e.g. RELIANCE)"
          aria-label="Filter by symbol"
          value={filters.symbol ?? ""}
          onChange={(e) =>
            set({ symbol: e.target.value.trim() || undefined })
          }
        />
        <select
          className="ui-select filter-input"
          aria-label="Max age"
          value={filters.max_age_hours ? String(filters.max_age_hours) : ""}
          onChange={(e) =>
            set({
              max_age_hours: e.target.value
                ? Number(e.target.value)
                : undefined,
            })
          }
        >
          {MAX_AGE.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <ul className="news-source-list">
        <li>
          <button
            className={`news-source-item ${selectedSource === "" ? "is-active" : ""}`}
            onClick={() => set({ source_ids: undefined })}
          >
            <span className="news-source-name">All sources</span>
            <span className="news-source-count">{sources.length}</span>
          </button>
        </li>
        {enabled.map((s) => (
          <li key={s.source_id}>
            <button
              className={`news-source-item ${selectedSource === s.source_id ? "is-active" : ""}`}
              onClick={() => toggleSource(s.source_id)}
            >
              <span className="news-source-name">{s.name}</span>
              <span className={`chip chip-muted news-source-type`}>
                {s.source_type}
              </span>
            </button>
          </li>
        ))}
        {enabled.length === 0 ? (
          <li className="muted news-source-empty">No enabled sources.</li>
        ) : null}
      </ul>
    </aside>
  );
}
