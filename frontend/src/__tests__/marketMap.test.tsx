import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import type * as RR from "react-router-dom";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

// MarketMapView is a pure React/CSS treemap (no echarts). Mock the data and
// side-effect hooks to test the current rendering/navigation contract.
vi.mock("@/api/market", () => ({
  getMarketMap: vi.fn(),
}));
vi.mock("@/features/analytics/useAnalyticsCoverage", () => ({
  useAnalyticsCoverage: vi.fn(),
}));

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = (await importOriginal()) as typeof RR;
  return { ...actual, useNavigate: () => mockNavigate };
});

// The treemap layout depends on element size reported by ResizeObserver.
// jsdom doesn't provide layout, so stub it to emit a usable size.
beforeAll(() => {
  class ResizeObserverStub {
    private cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) {
      this.cb = cb;
    }
    observe(_target: Element) {
      this.cb(
        [{ contentRect: { width: 800, height: 500 } } as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      );
    }
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", ResizeObserverStub);
});

import { getMarketMap } from "@/api/market";
import { MarketMapView } from "@/features/market-map/MarketMapView";

function renderWithProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/market-map"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const SNAP = {
  universe: "FNO",
  eligible: 2,
  quoted: 1,
  unavailable: 1,
  advances: 1,
  declines: 0,
  unchanged: 0,
  unclassified: 0,
  as_of: null,
  stale: false,
  sectors: [
    {
      sector: "ENERGY",
      stocks: [
        {
          symbol: "RELIANCE",
          exchange: "NSE",
          instrument_token: "1",
          sector: "ENERGY",
          ltp: 2500,
          change: 10,
          change_percent: 0.4,
          volume: 5_000_000,
          status: "advance",
          fno: true,
          received_ts: null,
        },
      ],
      advances: 1,
      declines: 0,
      unchanged: 0,
      unavailable: 0,
      quoted: 1,
    },
    {
      sector: "Unclassified",
      stocks: [
        {
          symbol: "X",
          exchange: "NSE",
          instrument_token: null,
          sector: "Unclassified",
          ltp: null,
          change: null,
          change_percent: null,
          volume: null,
          status: "unavailable",
          fno: false,
          received_ts: null,
        },
      ],
      advances: 0,
      declines: 0,
      unchanged: 0,
      unavailable: 1,
      quoted: 0,
    },
  ],
  reconciliation: {},
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MarketMapView", () => {
  it("renders the treemap tiles for the universe", async () => {
    (getMarketMap as ReturnType<typeof vi.fn>).mockResolvedValue(SNAP);
    renderWithProviders(<MarketMapView />);
    // Tiles render as buttons with the stock symbol.
    expect(await screen.findByRole("button", { name: /RELIANCE/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /X/ })).toBeTruthy();
    expect(screen.getByText("Market Heatmap")).toBeTruthy();
  });

  it("switches universe and refetches", async () => {
    (getMarketMap as ReturnType<typeof vi.fn>).mockResolvedValue(SNAP);
    renderWithProviders(<MarketMapView />);
    await screen.findByRole("button", { name: /RELIANCE/ });
    const select = (await screen.findByDisplayValue("FNO")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "NIFTY50" } });
    await waitFor(() =>
      expect(getMarketMap).toHaveBeenLastCalledWith("NIFTY50", expect.anything()),
    );
  });

  it("navigates to Charts preserving exact instrument identity on tile click", async () => {
    (getMarketMap as ReturnType<typeof vi.fn>).mockResolvedValue(SNAP);
    renderWithProviders(<MarketMapView />);
    const tile = await screen.findByRole("button", { name: /RELIANCE/ });
    fireEvent.click(tile);
    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith(
        expect.stringContaining("/chart?key=1&sym=RELIANCE&ex=NSE&type=EQUITY"),
      ),
    );
  });

  it("falls back to symbol resolution when the token is absent", async () => {
    (getMarketMap as ReturnType<typeof vi.fn>).mockResolvedValue(SNAP);
    renderWithProviders(<MarketMapView />);
    // The unavailable tile still renders and is clickable (symbol-only fallback).
    const tile = await screen.findByRole("button", { name: /^X/ });
    fireEvent.click(tile);
    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith(
        expect.stringContaining("/chart?sym=X&ex=NSE&type=EQUITY"),
      ),
    );
    expect(mockNavigate).not.toHaveBeenCalledWith(expect.stringContaining("key="));
  });

  it("shows error state", async () => {
    (getMarketMap as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("map failed"));
    renderWithProviders(<MarketMapView />);
    expect(await screen.findByText(/map failed/)).toBeInTheDocument();
  });
});
