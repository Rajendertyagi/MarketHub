import type { NewsFilters, NewsSource } from "../types";

const MAX_AGE = [
  { value: "", label: "Any age" },
  { value: "1", label: "Last hour" },
  { value: "6", label: "Last 6 hours" },
  { value: "24", label: "Last 24 hours" },
  { value: "72", label: "Last 3 days" },
  { value: "168", label: "Last 7 days" },
];

interface Props {
  filters: NewsFilters;
  sources: NewsSource[];
  onChange: (f: NewsFilters) => void;
}

// Filter bar for the Sentiment dashboard. Mirrors the legacy sentiment filters
// (source / category / symbol / age) and writes into a NewsFilters object.
export function SentimentFilters({ filters, sources, onChange }: Props) {
  const sourceId = filters.source_ids?.[0] ?? "";
  const category = filters.categories?.[0] ?? "";
  const maxAge = filters.max_age_hours ? String(filters.max_age_hours) : "";
  return (
    <div className="news-toolbar sentiment-toolbar">
      <h2>Market Sentiment</h2>
      <select
        className="ui-select filter-input"
        aria-label="Source"
        value={sourceId}
        onChange={(e) =>
          onChange({
            ...filters,
            source_ids: e.target.value ? [e.target.value] : undefined,
          })
        }
      >
        <option value="">All Sources</option>
        {sources
          .filter((s) => s.enabled)
          .map((s) => (
            <option key={s.source_id} value={s.source_id}>
              {s.name}
            </option>
          ))}
      </select>
      <input
        className="ui-input filter-input"
        type="text"
        placeholder="Category."
        aria-label="Category filter"
        value={category}
        onChange={(e) =>
          onChange({
            ...filters,
            categories: e.target.value ? [e.target.value] : undefined,
          })
        }
      />
      <input
        className="ui-input filter-input"
        type="text"
        placeholder="Symbol."
        aria-label="Symbol filter"
        value={filters.symbol ?? ""}
        onChange={(e) =>
          onChange({ ...filters, symbol: e.target.value || undefined })
        }
      />
      <select
        className="ui-select filter-input"
        aria-label="Max age filter"
        value={maxAge}
        onChange={(e) =>
          onChange({
            ...filters,
            max_age_hours: e.target.value ? Number(e.target.value) : undefined,
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
  );
}
