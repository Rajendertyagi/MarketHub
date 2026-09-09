import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const { echartsMock } = vi.hoisted(() => {
  const instance = {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    on: vi.fn(),
  };
  const init = vi.fn(() => instance);
  return { echartsMock: { init, instance } };
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

import { getBreadth } from "@/api/market";
import { BreadthView } from "@/features/breadth/BreadthView";

function renderWithProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/breadth"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const SNAP = {
  universe: "NIFTY50",
  eligible: 3,
  quoted: 2,
  unavailable: 1,
  advances: 1,
  declines: 1,
  unchanged: 0,
  advance_percent: 50,
  decline_percent: 50,
  ad_ratio: 1,
  net_advances: 0,
  as_of: "2024-01-01T00:00:00Z",
  unclassified: 0,
  stale: false,
  volume_advancing: null,
  volume_declining: null,
  intraday_highs: null,
  intraday_lows: null,
  weighting: "equal",
  rows: [
    {
      symbol: "RELIANCE",
      name: null,
      exchange: "NSE",
      instrument_token: "1",
      ltp: 2500,
      change: 10,
      change_percent: 0.4,
      status: "advance",
      sector: "ENERGY",
      received_ts: null,
    },
    {
      symbol: "INFY",
      name: null,
      exchange: "NSE",
      instrument_token: "2",
      ltp: 1500,
      change: -5,
      change_percent: -0.3,
      status: "decline",
      sector: "IT",
      received_ts: null,
    },
    {
      symbol: "TCS",
      name: null,
      exchange: "NSE",
      instrument_token: "3",
      ltp: null,
      change: null,
      change_percent: null,
      status: "unavailable",
      sector: "IT",
      received_ts: null,
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("BreadthView", () => {
  it("renders canonical breadth metrics", async () => {
    (getBreadth as ReturnType<typeof vi.fn>).mockResolvedValue(SNAP);
    renderWithProviders(<BreadthView />);
    expect(await screen.findByText("Advances")).toBeInTheDocument();
    // Summary cards show the canonical labels (Unavailable also appears as a
    // status-filter option, so assert at least one occurrence).
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    // Constituent rows render.
    expect(await screen.findByText("RELIANCE")).toBeInTheDocument();
    expect(screen.getByText("INFY")).toBeInTheDocument();
    expect(screen.getByText("TCS")).toBeInTheDocument();
  });

  it("switches universe and refetches", async () => {
    (getBreadth as ReturnType<typeof vi.fn>).mockResolvedValue(SNAP);
    renderWithProviders(<BreadthView />);
    await screen.findByText("RELIANCE");
    const select = (await screen.findByDisplayValue("NIFTY50")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "FNO" } });
    await waitFor(() =>
      expect(getBreadth).toHaveBeenLastCalledWith("FNO", expect.anything()),
    );
  });

  it("shows error state and retry", async () => {
    (getBreadth as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error("breadth failed"),
    );
    renderWithProviders(<BreadthView />);
    expect(await screen.findByText(/breadth failed/)).toBeInTheDocument();
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });

  it("shows empty state when no constituents", async () => {
    (getBreadth as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...SNAP,
      eligible: 0,
      rows: [],
    });
    renderWithProviders(<BreadthView />);
    expect(await screen.findByText(/No constituents/)).toBeInTheDocument();
  });

  it("requests analytics coverage for the active universe", async () => {
    (getBreadth as ReturnType<typeof vi.fn>).mockResolvedValue(SNAP);
    const { ensureAnalyticsCoverage } = await import("@/api/analytics");
    renderWithProviders(<BreadthView />);
    await screen.findByText("RELIANCE");
    expect(ensureAnalyticsCoverage).toHaveBeenCalledWith("NIFTY50");
  });
});
