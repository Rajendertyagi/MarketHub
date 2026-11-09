import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type * as RR from "react-router-dom";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ getFuturesHeatmap: vi.fn() }));
vi.mock("@/api/market", () => ({ getFuturesHeatmap: api.getFuturesHeatmap }));

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = (await importOriginal()) as typeof RR;
  return { ...actual, useNavigate: () => mockNavigate };
});

import { FuturesHeatmapView } from "@/features/futures-heatmap/FuturesHeatmapView";

const SNAP = {
  expiry_requested: "latest",
  expiry_resolved: "2026-09-29",
  expiries: ["2026-09-29", "2026-10-27"],
  buildup: "all",
  eligible: 2,
  quoted: 1,
  unavailable: 1,
  class_counts: { "Long Buildup": 1 },
  as_of: "2026-09-20T10:00:00+00:00",
  data_status: "stale",
  items: [
    {
      symbol: "NIFTY",
      kind: "index",
      exchange: "NSE",
      instrument_key: "NSE_FO|1",
      underlying: "NIFTY",
      expiry: "2026-09-29",
      futures_ltp: 23400,
      price_pct: 0.43,
      oi: 1000,
      previous_oi: 900,
      oi_change: 100,
      oi_pct: 11.1,
      buildup: "Long Buildup",
      spot_ltp: 23350,
      basis: 50,
      status: "classified",
      coverage: "live",
      oi_basis: "provider",
      provider: "upstox",
      received_ts: "2026-09-20T10:00:00+00:00",
    },
    {
      symbol: "RELIANCE",
      kind: "equity",
      exchange: "NSE",
      instrument_key: "NSE_FO|2",
      underlying: "RELIANCE",
      expiry: "2026-09-29",
      futures_ltp: null,
      price_pct: null,
      oi: null,
      previous_oi: null,
      oi_change: null,
      oi_pct: null,
      buildup: null,
      spot_ltp: null,
      basis: null,
      status: "unavailable",
      coverage: "no_data",
      oi_basis: "unavailable",
      provider: "upstox",
      received_ts: null,
    },
  ],
  providers: [
    {
      provider: "upstox",
      label: "Upstox",
      enabled: true,
      status: "streaming",
      available: true,
      capabilities: ["ltp", "oi"],
      budget: 1800,
    },
  ],
  coverage: {
    desired: 4,
    routed: 4,
    by_provider: { upstox: 4 },
    states: { live: 2, unsubscribed: 2 },
    capacity: { upstox: { budget: 1800, used: 4, available: 1796 } },
    uncovered: 0,
    errors: [],
  },
};

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/futures-heatmap"]}>
        <FuturesHeatmapView />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("FuturesHeatmapView", () => {
  it("renders the board, coverage summary and provider info", async () => {
    api.getFuturesHeatmap.mockResolvedValue(SNAP);
    renderView();
    expect(await screen.findByText("Futures Board")).toBeTruthy();
    expect(await screen.findByText("Upstox")).toBeTruthy();
    // one unfiltered fetch (buildup="all"), no per-underlying calls
    expect(api.getFuturesHeatmap).toHaveBeenCalledWith("latest", "all", expect.anything());
  });

  it("grid view renders uniform tiles with coverage badge for no_data", async () => {
    api.getFuturesHeatmap.mockResolvedValue(SNAP);
    renderView();
    expect(await screen.findByText("NIFTY")).toBeTruthy();
    expect(await screen.findByText("RELIANCE")).toBeTruthy();
    // no_data tile carries the honest "No data" badge (never "unavailable")
    expect((await screen.findAllByText("No data")).length).toBeGreaterThan(0);
    // tooltip (native title) carries the extra board fields
    const nifty = screen.getByText("NIFTY").closest("button");
    const title = nifty?.getAttribute("title") ?? "";
    expect(title).toMatch(/Prev OI/);
    expect(title).toMatch(/Buildup/);
    expect(title).toMatch(/Basis/);
  });

  it("table view shows canonical fields and coverage labels", async () => {
    api.getFuturesHeatmap.mockResolvedValue(SNAP);
    renderView();
    await screen.findByText("Futures Board");
    fireEvent.click(screen.getByText("Table"));
    for (const h of ["Prev OI", "OI Chg", "OI basis", "Basis", "Coverage"]) {
      expect((await screen.findAllByText(h)).length).toBeGreaterThan(0);
    }
    expect((await screen.findAllByText("Live")).length).toBeGreaterThan(0);
    expect((await screen.findAllByText("No data")).length).toBeGreaterThan(0);
  });

  it("filters client-side by buildup (no refetch)", async () => {
    api.getFuturesHeatmap.mockResolvedValue(SNAP);
    renderView();
    await screen.findByText("NIFTY");
    const longTab = screen.getAllByText("Long Buildup")[0];
    if (!longTab) throw new Error("expected Long Buildup tab");
    fireEvent.click(longTab);
    await waitFor(() => expect(screen.queryByText("RELIANCE")).toBeNull());
    expect(screen.getByText("NIFTY")).toBeTruthy();
    expect(api.getFuturesHeatmap).toHaveBeenCalledTimes(1);
  });

  it("filters client-side by coverage state", async () => {
    api.getFuturesHeatmap.mockResolvedValue(SNAP);
    renderView();
    await screen.findByText("NIFTY");
    const selects = screen.getAllByRole("combobox") as HTMLSelectElement[];
    // order: [Expiry, Coverage, Sort]
    const covSelect = selects[1];
    if (!covSelect) throw new Error("expected coverage select");
    fireEvent.change(covSelect, { target: { value: "no_data" } });
    await waitFor(() => expect(screen.queryByText("NIFTY")).toBeNull());
    expect(screen.getByText("RELIANCE")).toBeTruthy();
  });

  it("expiry selector uses API expiries (never hardcoded)", async () => {
    api.getFuturesHeatmap.mockResolvedValue(SNAP);
    renderView();
    expect(await screen.findByText("2026-10-27")).toBeTruthy();
  });

  it("surfaces load errors honestly", async () => {
    api.getFuturesHeatmap.mockRejectedValue(new Error("board down"));
    renderView();
    expect(await screen.findByText(/board down/)).toBeTruthy();
  });
});
