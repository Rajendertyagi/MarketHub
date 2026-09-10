import { Field, Input } from "@/components/ui";
import { DEFAULT_NEWS_LIMIT, MAX_NEWS_LIMIT } from "../constants";
import type { NewsFilters } from "../types";

interface NewsFiltersProps {
  filters: NewsFilters;
  onChange: (next: NewsFilters) => void;
}

// Controlled filter bar. Comma-separated keyword inputs are the only free-text
// entry points; numeric bounds are validated against shared constants.
export function NewsFiltersBar({ filters, onChange }: NewsFiltersProps) {
  const set = (patch: Partial<NewsFilters>) => onChange({ ...filters, ...patch });

  return (
    <div className="news-filters">
      <Field label="Symbol">
        <Input
          value={filters.symbol ?? ""}
          placeholder="e.g. RELIANCE"
          onChange={(e) => set({ symbol: e.target.value.trim() || undefined })}
        />
      </Field>
      <Field label="Keywords include">
        <Input
          value={(filters.keywords_include ?? []).join(", ")}
          placeholder="comma separated"
          onChange={(e) =>
            set({
              keywords_include: splitCsv(e.target.value),
            })
          }
        />
      </Field>
      <Field label="Keywords exclude">
        <Input
          value={(filters.keywords_exclude ?? []).join(", ")}
          placeholder="comma separated"
          onChange={(e) =>
            set({
              keywords_exclude: splitCsv(e.target.value),
            })
          }
        />
      </Field>
      <Field label="Max age (hours)">
        <Input
          type="number"
          min={0}
          value={filters.max_age_hours ?? ""}
          onChange={(e) =>
            set({
              max_age_hours:
                e.target.value === "" ? undefined : Number(e.target.value),
            })
          }
        />
      </Field>
      <Field label="Limit">
        <Input
          type="number"
          min={1}
          max={MAX_NEWS_LIMIT}
          value={filters.limit ?? DEFAULT_NEWS_LIMIT}
          onChange={(e) =>
            set({
              limit: Math.max(
                1,
                Math.min(MAX_NEWS_LIMIT, Number(e.target.value) || DEFAULT_NEWS_LIMIT),
              ),
            })
          }
        />
      </Field>
    </div>
  );
}

function splitCsv(value: string): string[] | undefined {
  const parts = value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  return parts.length ? parts : undefined;
}
