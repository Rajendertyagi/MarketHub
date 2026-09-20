import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

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

import { getSectorHeatmap } from "@/api/market";
import { SectorHeatmapView } from "@/features/sector-heatmap/SectorHeatmapView";

function renderWithProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/sector-heatmap"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const SNAP = {
  universe: "NIFTY50",
  sector_count: 2,
  classified_count: 1,
  unclassified_count: 1,
  eligible: 2,
  quoted: 1,
  unavailable: 1,
  advances: 1,
  declines: 0,
  unchanged: 0,
  as_of: null,
  weighting: "equal",
  stale: false,
  sectors: [
    {
      sector: "ENERGY",
      constituent_count: 1,
      quoted: 1,
      unavailable: 0,
      advances: 1,
      declines: 0,
      unchanged: 0,
      average_change_percent: 0.4,
      median_change_percent: 0.4,
      net_advances: 1,
      top_gainer: null,
      top_loser: null,
      weighting: "equal",
      members: [
        {
          symbol: "RELIANCE",
          name: null,
          ltp: 2500,
          change: 10,
          change_percent: 0.4,
          volume: 1000,
          status: "advance",
          fno: true,
        },
      ],
    },
    {
      sector: "Unclassified",
      constituent_count: 1,
      quoted: 0,
      unavailable: 1,
      advances: 0,
      declines: 0,
      unchanged: 0,
      average_change_percent: null,
      median_change_percent: null,
      net_advances: 0,
      top_gainer: null,
      top_loser: null,
      weighting: "equal",
      members: [
        {
          symbol: "X",
          name: null,
          ltp: null,
          change: null,
          change_percent: null,
          volume: null,
          status: "unavailable",
          fno: false,
        },
      ],
    },
  ],
  reconciliation: {},
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SectorHeatmapView", () => {
  it("renders the heatmap and summary for the universe", async () => {
    (getSectorHeatmap as ReturnType<typeof vi.fn>).mockResolvedValue(SNAP);
    renderWithProviders(<SectorHeatmapView />);
    // Chart rendered (treemap built from canonical sectors).
    await waitFor(() => expect(echartsMock.init).toHaveBeenCalled());
    // Summary reflects canonical counts (Unclassified preserved as a stat).
    expect(await screen.findByText("Unclassified")).toBeInTheDocument();
    expect(screen.getByText("Sector Heatmap")).toBeInTheDocument();
    expect(screen.getByText("Advances")).toBeInTheDocument();
  });

  it("switches universe and refetches", async () => {
    (getSectorHeatmap as ReturnType<typeof vi.fn>).mockResolvedValue(SNAP);
    renderWithProviders(<SectorHeatmapView />);
    await waitFor(() => expect(echartsMock.init).toHaveBeenCalled());
    const select = (await screen.findByDisplayValue("NIFTY50")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "FNO" } });
    await waitFor(() =>
      expect(getSectorHeatmap).toHaveBeenLastCalledWith("FNO", expect.anything()),
    );
  });

  it("shows error state", async () => {
    (getSectorHeatmap as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("heatmap failed"));
    renderWithProviders(<SectorHeatmapView />);
    expect(await screen.findByText(/heatmap failed/)).toBeInTheDocument();
  });

  it("shows empty state when no sectors", async () => {
    (getSectorHeatmap as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...SNAP,
      sector_count: 0,
      sectors: [],
    });
    renderWithProviders(<SectorHeatmapView />);
    expect(await screen.findByText(/No sectors/)).toBeInTheDocument();
  });
});
