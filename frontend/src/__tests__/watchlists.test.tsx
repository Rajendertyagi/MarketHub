import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/market", () => ({
  searchInstruments: vi.fn(),
}));

vi.mock("@/features/watchlists/api", () => ({
  getWatchlists: vi.fn(),
  createWatchlist: vi.fn(),
  renameWatchlist: vi.fn(),
  deleteWatchlist: vi.fn(),
  addWatchlistItem: vi.fn(),
  removeWatchlistItem: vi.fn(),
}));

import { searchInstruments } from "@/api/market";
import { addWatchlistItem } from "@/features/watchlists/api";
import { AddInstrument } from "@/features/watchlists/components/AddInstrument";
import { WatchlistTable } from "@/features/watchlists/components/WatchlistTable";
import { ApiError } from "@/types";

function renderWithProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/watchlists"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const RELIANCE = {
  instrument_token: "NSE_EQ:RELIANCE",
  exchange: "NSE",
  tradingsymbol: "RELIANCE",
  instrument_type: "EQUITY",
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AddInstrument", () => {
  it("searches the catalog and adds the picked instrument", async () => {
    (searchInstruments as ReturnType<typeof vi.fn>).mockResolvedValue([RELIANCE]);
    (addWatchlistItem as ReturnType<typeof vi.fn>).mockResolvedValue({
      status: "ok",
    });

    renderWithProviders(<AddInstrument watchlistId={7} existingTokens={new Set()} />);

    fireEvent.change(screen.getByLabelText("Search instruments to add to watchlist"), {
      target: { value: "RELI" },
    });

    const opt = await screen.findByRole("button", { name: /RELIANCE/ });
    expect(opt).toHaveTextContent("RELIANCE");
    fireEvent.click(opt);

    fireEvent.click(screen.getByRole("button", { name: /add/i }));

    await waitFor(() =>
      expect(addWatchlistItem).toHaveBeenCalledWith(7, {
        exchange: "NSE",
        instrument_token: "NSE_EQ:RELIANCE",
        tradingsymbol: "RELIANCE",
      }),
    );
    expect(await screen.findByText("Added RELIANCE.")).toBeInTheDocument();
  });

  it("surfaces an already-in-watchlist duplicate as a note, not an error", async () => {
    (searchInstruments as ReturnType<typeof vi.fn>).mockResolvedValue([RELIANCE]);
    (addWatchlistItem as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError("http", "already in watchlist", { status: 409 }),
    );

    renderWithProviders(<AddInstrument watchlistId={7} existingTokens={new Set()} />);

    fireEvent.change(screen.getByLabelText("Search instruments to add to watchlist"), {
      target: { value: "RELI" },
    });
    fireEvent.click(await screen.findByRole("button", { name: /RELIANCE/ }));
    fireEvent.click(screen.getByRole("button", { name: /add/i }));

    expect(await screen.findByText("Already in this watchlist.")).toBeInTheDocument();
  });
});

describe("WatchlistTable", () => {
  it("links symbols to Charts and joins quotes without per-row scans", () => {
    const items = [
      {
        id: 1,
        watchlist_id: 7,
        exchange: "NSE",
        instrument_token: "NSE_EQ:RELIANCE",
        tradingsymbol: "RELIANCE",
      },
    ];
    const quotes = [
      {
        exchange: "NSE",
        instrument_token: "NSE_EQ:RELIANCE",
        tradingsymbol: "RELIANCE",
        ltp: 2500,
        change: 10,
        change_percent: 0.4,
        volume: 1000,
        best_bid: 2499,
        best_ask: 2501,
        received_ts: new Date().toISOString(),
      },
    ];

    renderWithProviders(<WatchlistTable items={items} quotes={quotes} onRemove={() => {}} />);

    const link = screen.getByRole("link", { name: "RELIANCE" });
    expect(link.getAttribute("href")).toContain("/charts?");
    expect(link.getAttribute("href")).toContain(`key=${encodeURIComponent("NSE_EQ:RELIANCE")}`);
    expect(screen.getByText("2,500.00")).toBeInTheDocument();
  });

  it("points at the add box when empty", () => {
    renderWithProviders(<WatchlistTable items={[]} quotes={[]} onRemove={() => {}} />);
    expect(screen.getByText(/Search above to add instruments/)).toBeInTheDocument();
  });
});
