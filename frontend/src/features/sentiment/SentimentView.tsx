import { useState } from "react";
import { ApiError } from "@/types";
import { AsyncStateView, Button } from "@/components/ui";
import {
  useSentiment,
  useSentimentRefresh,
  useSentimentSources,
} from "./useSentiment";
import type { NewsFilters } from "@/features/news/types";
import {
  aggregateSentiment,
  articleCategory,
  buildSentimentMap,
  groupBySentiment,
} from "./aggregate";
import { SentimentSummary } from "./components/SentimentSummary";
import { SentimentBreakdown } from "./components/SentimentBreakdown";
import { SentimentItems } from "./components/SentimentItems";
import { SentimentFilters } from "./components/SentimentFilters";

// Standalone Market Sentiment dashboard — the aggregate view the legacy app
// exposed as its own nav item. Reuses the News sentiment endpoint.
export function SentimentView() {
  const [filters, setFilters] = useState<NewsFilters>({});
  const query = useSentiment(filters);
  const sourcesQuery = useSentimentSources();
  const refresh = useSentimentRefresh();
  const [msg, setMsg] = useState<string | null>(null);

  const data = query.data;
  const articles = data?.articles ?? [];
  const byId = buildSentimentMap(data?.sentiments ?? []);
  const summary = aggregateSentiment(articles, byId);
  const bySource = groupBySentiment(articles, byId, (a) => a.source_name);
  const byCategory = groupBySentiment(articles, byId, articleCategory);

  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">Market Sentiment</h1>
        <span className="muted">
          Aggregated from persisted news sentiment — React renders only
        </span>
      </div>

      <SentimentFilters
        filters={filters}
        sources={sourcesQuery.data?.sources ?? []}
        onChange={setFilters}
      />

      <div className="control-row">
        <Button
          variant="primary"
          onClick={async () => {
            setMsg(null);
            try {
              const res = (await refresh.mutateAsync()) as { status: string };
              setMsg(`Refresh ${res.status}`);
            } catch (e) {
              setMsg(e instanceof Error ? e.message : "refresh failed");
            }
          }}
        >
          Refresh sources
        </Button>
        {msg ? <span className="hint ok">{msg}</span> : null}
      </div>

      {query.isLoading ? (
        <AsyncStateView status="loading" loadingLabel="Loading sentiment…" />
      ) : null}
      {query.isError ? (
        <AsyncStateView
          status="error"
          error={query.error as ApiError}
          onRetry={() => query.refetch()}
        />
      ) : null}

      {query.data ? (
        <>
          <SentimentSummary
            summary={summary}
            total={summary.count}
            sources={(data?.sources_queried ?? []).length}
          />
          <div className="sentiment-grid">
            <SentimentBreakdown title="By Source" groups={bySource} />
            <SentimentBreakdown title="By Category" groups={byCategory} />
          </div>
          <SentimentItems articles={articles} byId={byId} />
        </>
      ) : null}
    </div>
  );
}
