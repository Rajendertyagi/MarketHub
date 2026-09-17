import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api/market", () => ({
  runScanner: vi.fn(),
  getSubscriptions: vi.fn(),
}));

import { runScanner, getSubscriptions } from "@/api/market";
import { StreamStatus } from "@/components/StreamStatus";
import { TickerStrip } from "@/features/dashboard/components/TickerStrip";
import { IndicesWidget } from "@/features/dashboard/components/IndicesWidget";
import { MoversWidget } from "@/features/dashboard/components/MoversWidget";

function renderWithProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const FRESH_TS = new Date().toISOString();
const STALE_TS = new Date(Date.now() - 10 * 60_000).toISOString();

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("StreamStatus", () => {
  it("labels live vs reconnecting", () => {
    const { rerender } = render(<StreamStatus connected />);
    expect(screen.getByRole("status")).toHaveTextContent("● Live");
    rerender(<StreamStatus connected={false} />);
    expect(screen.getByRole("status")).toHaveTextContent("● Reconnecting");
  });
});

describe("TickerStrip staleness", () => {
  const quote = (ts: string) => ({
    exchange: "NSE",
    instrument_token: "NSE:NIFTY50",
    tradingsymbol: "NIFTY50",
    ltp: 25000,
    change: 10,
    change_percent: 0.04,
    volume: 1000,
    received_ts: ts,
  });

  it("tags stale quotes explicitly", () => {
    render(<TickerStrip quotes={[quote(STALE_TS)]} />);
    expect(screen.getByText("stale")).toBeInTheDocument();
  });

  it("leaves fresh quotes untagged", () => {
    render(<TickerStrip quotes={[quote(FRESH_TS)]} />);
    expect(screen.queryByText("stale")).not.toBeInTheDocument();
  });
});

describe("IndicesWidget staleness", () => {
  it("marks stale index cards in the accessible label", async () => {
    (getSubscriptions as ReturnType<typeof vi.fn>).mockResolvedValue({
      indices: [{ label: "NIFTY 50", key: "NSE:NIFTY50", enabled: true }],
    });
    const quotes = [
      {
        exchange: "NSE",
        instrument_token: "NSE:NIFTY50",
        tradingsymbol: "NIFTY 50",
        ltp: 25000,
        change: 10,
        change_percent: 0.04,
        received_ts: STALE_TS,
      },
    ];
    renderWithProviders(<IndicesWidget quotes={quotes} />);
    const card = await screen.findByRole("link", { name: /NIFTY 50.*stale data/ });
    expect(card).toHaveTextContent("stale");
  });
});

describe("MoversWidget", () => {
  const gainers = {
    scanner: "gainers",
    universe: "FNO",
    as_of: null,
    eligible: 2,
    quoted: 2,
    matched: 2,
    rows: [
      {
        symbol: "RELIANCE",
        sector: "Energy",
        ltp: 2500,
        change: 50,
        change_percent: 2.04,
        volume: 1000,
        oi: null,
        iv: null,
        status: "ok",
        freshness: "live",
      },
    ],
  };
  const losers = {
    ...gainers,
    scanner: "losers",
    rows: [
      {
        ...gainers.rows[0],
        symbol: "INFY",
        change: -30,
        change_percent: -1.2,
      },
    ],
  };

  it("renders gainers and switches category on tab select", async () => {
    (runScanner as ReturnType<typeof vi.fn>).mockImplementation(
      (name: string) => Promise.resolve(name === "losers" ? losers : gainers),
    );

    renderWithProviders(<MoversWidget />);

    expect(await screen.findByText("RELIANCE")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Top Loser" }));
    expect(await screen.findByText("INFY")).toBeInTheDocument();
    expect(runScanner).toHaveBeenCalledWith(
      "losers",
      { universe: "FNO", limit: 5 },
      expect.anything(),
    );
  });
});
