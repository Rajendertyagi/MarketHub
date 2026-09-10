import { useState } from "react";
import { ApiError } from "@/types";
import { AsyncStateView, Button, Tabs } from "@/components/ui";
import { NEWS_TABS, type NewsTab } from "./constants";
import { useNews, useNewsMutations, useNewsSentiment } from "./useNews";
import type { NewsFilters } from "./types";
import { NewsFiltersBar } from "./components/NewsFilters";
import { NewsList } from "./components/NewsList";
import { SentimentPanel } from "./components/SentimentPanel";
import { SourcesManager } from "./components/SourcesManager";

export function NewsView() {
  const [tab, setTab] = useState<NewsTab>("news");
  const [filters, setFilters] = useState<NewsFilters>({});
  const [refreshMsg, setRefreshMsg] = useState<string | null>(null);

  const newsQuery = useNews(filters);
  const sentimentQuery = useNewsSentiment(filters);
  const m = useNewsMutations();

  const active = tab === "sentiment" ? sentimentQuery : newsQuery;

  let body: React.ReactNode;
  if (tab === "sources") {
    body = <SourcesManager />;
  } else if (active.isLoading) {
    body = <AsyncStateView status="loading" loadingLabel="Loading news…" />;
  } else if (active.isError) {
    body = (
      <AsyncStateView
        status="error"
        error={active.error as ApiError}
        onRetry={() => active.refetch()}
      />
    );
  } else if (tab === "sentiment") {
    const data = sentimentQuery.data;
    body = (
      <SentimentPanel
        articles={data?.articles ?? []}
        sentiments={data?.sentiments ?? []}
      />
    );
  } else {
    body = <NewsList articles={newsQuery.data?.articles ?? []} />;
  }

  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">News &amp; Sentiment</h1>
        <span className="muted">
          Backend-ingested articles &amp; sentiment — React renders only
        </span>
      </div>

      <Tabs<NewsTab>
        tabs={NEWS_TABS.map((t) => ({ value: t.value, label: t.label }))}
        active={tab}
        onChange={setTab}
      />

      {tab !== "sources" && (
        <>
          <NewsFiltersBar filters={filters} onChange={setFilters} />
          <div className="control-row">
            <Button
              variant="primary"
              onClick={async () => {
                setRefreshMsg(null);
                try {
                  const res = (await m.refresh()) as { status: string };
                  setRefreshMsg(`Refresh ${res.status}`);
                } catch (e) {
                  setRefreshMsg(e instanceof Error ? e.message : "refresh failed");
                }
              }}
            >
              Refresh sources
            </Button>
            {refreshMsg ? <span className="hint ok">{refreshMsg}</span> : null}
          </div>
        </>
      )}

      {body}
    </div>
  );
}
