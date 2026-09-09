import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import * as RR from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const { echartsMock } = vi.hoisted(() => {
  const onHandlers: Record<string, (p: unknown) => void> = {};
  const instance = {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    on: vi.fn((ev: string, cb: (p: unknown) => void) => {
      onHandlers[ev] = cb;
    }),
  };
  const init = vi.fn(() => instance);
  return { echartsMock: { init, instance, onHandlers } };
});

vi.mock("echarts", () => ({ init: echartsMock.init }));

vi.mock("@/api/market", () => ({
  getHistory: vi.fn(),
  listScanners: vi.fn(),
  runScanner: vi.fn(),
  searchInstruments: vi.fn(),
  resolveInstrument: vi.fn(),
  getBreadth: vi.fn(),
  getSectorHeatmap: vi.fn(),
  getMarketMap: vi.fn(),
}));

vi.mock("@/api/analytics", () => ({
  ensureAnalyticsCoverage: vi.fn(() => Promise.resolve()),
  clearAnalyticsCoverage: vi.fn(() => Promise.resolve()),
}));

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = (await importOriginal()) as typeof RR;
  return { ...actual, useNavigate: () => mockNavigate };
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
  echartsMock.onHandlers.click = undefined as unknown as (p: unknown) => void;
});

describe("MarketMapView", () => {
  it("renders the treemap for the universe", async () => {
    (getMarketMap as ReturnType<typeof vi.fn>).mockResolvedValue(SNAP);
    renderWithProviders(<MarketMapView />);
    await waitFor(() => expect(echartsMock.init).toHaveBeenCalled());
    // The canonical snapshot was mapped into the chart option.
    expect(echartsMock.instance.setOption).toHaveBeenCalled();
  });

  it("switches universe and refetches", async () => {
    (getMarketMap as ReturnType<typeof vi.fn>).mockResolvedValue(SNAP);
    renderWithProviders(<MarketMapView />);
    await waitFor(() => expect(echartsMock.init).toHaveBeenCalled());
    const select = (await screen.findByDisplayValue("FNO")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "NIFTY50" } });
    await waitFor(() =>
      expect(getMarketMap).toHaveBeenLastCalledWith("NIFTY50", expect.anything()),
    );
  });

  it("navigates to Charts preserving exact instrument identity on tile click", async () => {
    (getMarketMap as ReturnType<typeof vi.fn>).mockResolvedValue(SNAP);
    renderWithProviders(<MarketMapView />);
    await waitFor(() => expect(echartsMock.onHandlers.click).toBeTypeOf("function"));
    echartsMock.onHandlers.click!({
      data: {
        _meta: {
          symbol: "RELIANCE",
          exchange: "NSE",
          token: "1",
          change: 0.4,
          status: "advance",
          fno: true,
        },
      },
    });
    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith(
        expect.stringContaining("/charts?key=1&sym=RELIANCE&ex=NSE&type=EQUITY"),
      ),
    );
  });

  it("falls back to symbol resolution when the token is absent", async () => {
    (getMarketMap as ReturnType<typeof vi.fn>).mockResolvedValue(SNAP);
    renderWithProviders(<MarketMapView />);
    await waitFor(() => expect(echartsMock.onHandlers.click).toBeTypeOf("function"));
    echartsMock.onHandlers.click!({
      data: {
        _meta: {
          symbol: "X",
          exchange: "NSE",
          token: null,
          change: null,
          status: "unavailable",
          fno: false,
        },
      },
    });
    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith(
        expect.stringContaining("/charts?sym=X&ex=NSE&type=EQUITY"),
      ),
    );
    expect(mockNavigate).not.toHaveBeenCalledWith(
      expect.stringContaining("key="),
    );
  });

  it("shows error state", async () => {
    (getMarketMap as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("map failed"),
    );
    renderWithProviders(<MarketMapView />);
    expect(await screen.findByText(/map failed/)).toBeInTheDocument();
  });
});
