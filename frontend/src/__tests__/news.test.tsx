import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const api = vi.hoisted(() => ({
  getNews: vi.fn(),
  getNewsSentiment: vi.fn(),
  getNewsSources: vi.fn(),
  refreshNews: vi.fn(),
  createNewsSource: vi.fn(),
  updateNewsSource: vi.fn(),
  deleteNewsSource: vi.fn(),
  setNewsSourceEnabled: vi.fn(),
  testNewsSource: vi.fn(),
}));
vi.mock("@/features/news/api", () => ({
  getNews: api.getNews,
  getNewsSentiment: api.getNewsSentiment,
  getNewsSources: api.getNewsSources,
  refreshNews: api.refreshNews,
  createNewsSource: api.createNewsSource,
  updateNewsSource: api.updateNewsSource,
  deleteNewsSource: api.deleteNewsSource,
  setNewsSourceEnabled: api.setNewsSourceEnabled,
  testNewsSource: api.testNewsSource,
}));

import { NewsView } from "@/features/news";

const RSS_ARTICLE = {
  type: "rss",
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
  type: "reddit",
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
  source_type: "rss",
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

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("NewsView", () => {
  it("renders RSS and Reddit articles from the backend (exact identity)", async () => {
    api.getNews.mockResolvedValue({
      status: "ok",
      count: 2,
      sources_queried: ["src-a", "src-b"],
      articles: [RSS_ARTICLE, REDDIT_ARTICLE],
    });
    renderWithProviders(<NewsView />);
    expect(await screen.findByText("Markets close higher")).toBeTruthy();
    expect(screen.getByText("My portfolio review")).toBeTruthy();
    expect(screen.getByText("MoneyControl")).toBeTruthy();
    expect(screen.getByText("r/IndiaInvestments")).toBeTruthy();
  });

  it("shows error state on load failure", async () => {
    api.getNews.mockRejectedValue(new Error("news load failed"));
    renderWithProviders(<NewsView />);
    expect(await screen.findByText(/news load failed/)).toBeTruthy();
  });

  it("builds the correct filter query (symbol + keywords + limit)", async () => {
    api.getNews.mockResolvedValue({
      status: "ok",
      count: 0,
      sources_queried: [],
      articles: [],
    });
    renderWithProviders(<NewsView />);
    await screen.findByText("News & Sentiment");
    const symbol = screen.getByPlaceholderText("e.g. RELIANCE") as HTMLInputElement;
    fireEvent.change(symbol, { target: { value: "RELIANCE" } });
    const kw = screen.getByPlaceholderText("comma separated") as HTMLInputElement;
    fireEvent.change(kw, { target: { value: "gain, rise" } });
    await waitFor(() =>
      expect(api.getNews).toHaveBeenLastCalledWith(
        expect.objectContaining({
          symbol: "RELIANCE",
          keywords_include: ["gain", "rise"],
        }),
        expect.anything(),
      ),
    );
  });

  it("refresh triggers a sync of sources", async () => {
    api.getNews.mockResolvedValue({ status: "ok", count: 0, sources_queried: [], articles: [] });
    api.refreshNews.mockResolvedValue({ status: "ok", fetched: 5 });
    renderWithProviders(<NewsView />);
    await screen.findByText("News & Sentiment");
    fireEvent.click(screen.getByText("Refresh sources"));
    await waitFor(() => expect(api.refreshNews).toHaveBeenCalled());
    expect(await screen.findByText(/Refresh ok/)).toBeTruthy();
  });

  it("sentiment tab pairs articles with persisted scores", async () => {
    api.getNewsSentiment.mockResolvedValue({
      status: "ok",
      count: 1,
      sources_queried: ["src-a"],
      articles: [RSS_ARTICLE],
      sentiments: [SENTIMENT],
    });
    renderWithProviders(<NewsView />);
    fireEvent.click(await screen.findByText("Sentiment"));
    expect(await screen.findByText("positive 0.80")).toBeTruthy();
    expect(screen.getByText(/matched: gain/)).toBeTruthy();
  });

  it("manages sources: enable toggle, add, delete", async () => {
    api.getNewsSources.mockResolvedValue({ status: "ok", sources: [SOURCE] });
    api.setNewsSourceEnabled.mockResolvedValue({ status: "ok" });
    api.createNewsSource.mockResolvedValue({ status: "ok" });
    api.deleteNewsSource.mockResolvedValue({ status: "ok", deleted: "src-a" });
    renderWithProviders(<NewsView />);
    fireEvent.click(await screen.findByText("Sources"));
    expect(await screen.findByText("MoneyControl")).toBeTruthy();

    // Toggle enabled off.
    const toggle = screen.getByRole("checkbox") as HTMLInputElement;
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(api.setNewsSourceEnabled).toHaveBeenCalledWith({
        id: "src-a",
        enabled: false,
      }),
    );

    // Add a new source.
    fireEvent.change(screen.getByPlaceholderText("My RSS Feed"), {
      target: { value: "New Feed" },
    });
    fireEvent.click(within(screen.getByText("Add news source").closest(".card")!).getByText("Add"));
    await waitFor(() =>
      expect(api.createNewsSource).toHaveBeenCalledWith(
        expect.objectContaining({ name: "New Feed", source_type: "rss" }),
      ),
    );

    // Delete existing source.
    fireEvent.click(screen.getAllByText("Delete")[0]!);
    await waitFor(() => expect(api.deleteNewsSource).toHaveBeenCalledWith("src-a"));
  });
});
