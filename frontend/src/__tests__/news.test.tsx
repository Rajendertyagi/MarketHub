import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

// NewsView consumes the hook layer (useNewsSentiment/useNewsSources/
// useNewsMutations). Mock the hooks to test the current 3-column reader UI
// contract without coupling to hook internals or module-resolution quirks.
const hooks = vi.hoisted(() => ({
  useNewsSentiment: vi.fn(),
  useNewsSources: vi.fn(),
  useNewsMutations: vi.fn(),
}));
vi.mock("@/features/news/useNews", () => ({
  useNewsSentiment: hooks.useNewsSentiment,
  useNewsSources: hooks.useNewsSources,
  useNewsMutations: hooks.useNewsMutations,
}));

import { NewsView } from "@/features/news";

const RSS_ARTICLE = {
  type: "rss" as const,
  item_id: "rss-1",
  source_id: "src-a",
  source_name: "MoneyControl",
  title: "Markets close higher",
  link: "https://example.com/a",
  published: "2026-01-01T10:00:00Z",
  summary: "Sensex gained 200 pts",
  author: "Desk",
};
const REDDIT_ARTICLE = {
  type: "reddit" as const,
  item_id: "rd-1",
  source_id: "src-b",
  source_name: "r/IndiaInvestments",
  subreddit: "IndiaInvestments",
  title: "My portfolio review",
  score: 42,
  num_comments: 10,
  author: "user1",
  url: "https://reddit.com/x",
  permalink: "https://reddit.com/r/x",
  created_utc: "2026-01-01T09:00:00Z",
  selftext: "Thoughts?",
  upvote_ratio: 0.9,
};
const SENTIMENT = {
  item_id: "rss-1",
  sentiment: "positive",
  score: 0.8,
  matched_keywords: ["gain"],
};
const SOURCE = {
  source_id: "src-a",
  name: "MoneyControl",
  source_type: "rss" as const,
  category: "general",
  enabled: true,
  config_json: { url: "https://example.com/feed" },
  created_at: "2026-01-01T00:00:00Z",
  updated_at: null,
};

function renderWithProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/news"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function setupHooks(overrides?: {
  news?: Partial<ReturnType<typeof hooks.useNewsSentiment>>;
  sources?: Partial<ReturnType<typeof hooks.useNewsSources>>;
  mutations?: Partial<ReturnType<typeof hooks.useNewsMutations>>;
}) {
  const refetch = vi.fn().mockResolvedValue(undefined);
  const refresh = vi.fn().mockResolvedValue({ status: "ok" });
  const setSourceEnabled = vi.fn().mockResolvedValue({ status: "ok" });
  const createSource = vi.fn().mockResolvedValue({ status: "ok" });
  const deleteSource = vi.fn().mockResolvedValue({ status: "ok", deleted: "src-a" });
  hooks.useNewsSentiment.mockReturnValue({
    data: { articles: [], sentiments: [], count: 0, sources_queried: [] },
    isLoading: false,
    isError: false,
    error: null,
    refetch,
    ...overrides?.news,
  });
  hooks.useNewsSources.mockReturnValue({
    data: { sources: [] },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides?.sources,
  });
  hooks.useNewsMutations.mockReturnValue({
    refresh,
    createSource,
    updateSource: vi.fn(),
    deleteSource,
    setSourceEnabled,
    testSource: vi.fn(),
    ...overrides?.mutations,
  });
  return { refetch, refresh, setSourceEnabled, createSource, deleteSource };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("NewsView", () => {
  it("renders RSS and Reddit articles with source identity and sentiment", async () => {
    setupHooks({
      news: {
        data: {
          articles: [RSS_ARTICLE, REDDIT_ARTICLE],
          sentiments: [SENTIMENT],
          count: 2,
          sources_queried: ["src-a", "src-b"],
        },
      },
      sources: { data: { sources: [SOURCE] } },
    });
    renderWithProviders(<NewsView />);
    // Title appears in the article list and the reader pane (selected article).
    expect((await screen.findAllByText("Markets close higher")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("My portfolio review").length).toBeGreaterThan(0);
    // Source identity appears in the list/reader; sentiment is rendered.
    expect(screen.getAllByText("MoneyControl").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/positive/).length).toBeGreaterThan(0);
  });

  it("shows error state on load failure", async () => {
    setupHooks({
      news: {
        data: undefined,
        isError: true,
        error: { message: "news load failed" },
      },
    });
    renderWithProviders(<NewsView />);
    expect(await screen.findByText(/news load failed/)).toBeTruthy();
  });

  it("applies symbol and keyword filters through the sources pane", async () => {
    setupHooks();
    renderWithProviders(<NewsView />);
    await screen.findByText("News");
    const symbol = screen.getByLabelText("Filter by symbol") as HTMLInputElement;
    fireEvent.change(symbol, { target: { value: "RELIANCE" } });
    const search = screen.getByLabelText("Search articles") as HTMLInputElement;
    fireEvent.change(search, { target: { value: "gain rise" } });
    await waitFor(() => {
      const last = hooks.useNewsSentiment.mock.calls.at(-1)?.[0];
      expect(last?.symbol).toBe("RELIANCE");
      expect(last?.keywords_include).toEqual(["gain", "rise"]);
    });
  });

  it("refresh triggers a source sync and refetches articles", async () => {
    const { refresh, refetch } = setupHooks();
    renderWithProviders(<NewsView />);
    await screen.findByText("News");
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    await waitFor(() => expect(refetch).toHaveBeenCalled());
  });

  it("shows persisted sentiment and matched keywords in the reader", async () => {
    setupHooks({
      news: {
        data: {
          articles: [RSS_ARTICLE],
          sentiments: [SENTIMENT],
          count: 1,
          sources_queried: ["src-a"],
        },
      },
    });
    renderWithProviders(<NewsView />);
    expect((await screen.findAllByText("Markets close higher")).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/positive/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Matched:/)).toBeTruthy();
    expect(screen.getByText("gain")).toBeTruthy();
  });

  it("manages sources via the modal: enable toggle, add, delete", async () => {
    const { setSourceEnabled, createSource, deleteSource } = setupHooks({
      sources: { data: { sources: [SOURCE] } },
    });
    renderWithProviders(<NewsView />);
    await screen.findByText("News");
    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    const dialog = await screen.findByRole("dialog", { name: "Manage news sources" });

    // Toggle enabled off (scoped to the sources table, not the add form).
    const table = within(dialog).getByRole("table");
    const toggle = within(table).getByRole("checkbox") as HTMLInputElement;
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(setSourceEnabled).toHaveBeenCalledWith({ id: "src-a", enabled: false }),
    );

    // Add a new source.
    fireEvent.change(within(dialog).getByPlaceholderText("My RSS Feed"), {
      target: { value: "New Feed" },
    });
    fireEvent.change(within(dialog).getByPlaceholderText("my-rss-feed"), {
      target: { value: "new-feed" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(createSource).toHaveBeenCalledWith(
        expect.objectContaining({ name: "New Feed", source_type: "rss" }),
      ),
    );

    // Delete existing source.
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(deleteSource).toHaveBeenCalledWith("src-a"));
  });
});
