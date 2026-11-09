import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getSubscriptions: vi.fn(),
  setIndexEnabled: vi.fn(),
  addStock: vi.fn(),
  setStockEnabled: vi.fn(),
  removeStock: vi.fn(),
  putDerivativeRule: vi.fn(),
  deleteDerivativeRule: vi.fn(),
  applySubscriptions: vi.fn(),
  getSegments: vi.fn(),
  getSyncState: vi.fn(),
  setSegments: vi.fn(),
  syncInstruments: vi.fn(),
  getFnoCoverage: vi.fn(),
  previewFnoCoverage: vi.fn(),
  applyFnoCoverage: vi.fn(),
  releaseFnoCoverage: vi.fn(),
  getFnoCatalog: vi.fn(),
}));
vi.mock("@/api/market", () => api);

import { MarketDataView } from "@/features/market-data/MarketDataView";

function coverage(overrides: Record<string, unknown> = {}) {
  return {
    enabled: true,
    expiry_requested: "latest",
    expiry_resolved: "2026-09-29",
    expiries: ["2026-09-29", "2026-10-27"],
    desired: 432,
    routed: 432,
    by_provider: { upstox: 312, fyers: 120 },
    states: { unsubscribed: 432 },
    capacity: {
      upstox: { budget: 1800, used: 312, available: 1488 },
      fyers: { budget: 4500, used: 120, available: 4380 },
    },
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
      {
        provider: "fyers",
        label: "Fyers",
        enabled: true,
        status: "streaming",
        available: true,
        capabilities: ["ltp", "oi", "previous_oi"],
        budget: 4500,
      },
    ],
    uncovered: [],
    errors: [],
    ...overrides,
  };
}

function renderAt(ui: ReactNode, path = "/market-data") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function primeCatalogMocks() {
  api.getSegments.mockResolvedValue({
    segments: [{ segment: "NSE_FO", catalog_rows: 10, default: true }],
    enabled: ["NSE_FO"],
  });
  api.getSyncState.mockResolvedValue({
    providers: [{ provider: "upstox", status: "ok", last_sync: null, rows: 10, error: null }],
  });
  api.getSubscriptions.mockResolvedValue({
    indices: [],
    stocks: [],
    derivatives: [],
  });
  api.getFnoCoverage.mockResolvedValue(coverage());
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("MarketDataView (unified page)", () => {
  it("shows Catalog and Live Coverage sections", async () => {
    primeCatalogMocks();
    renderAt(<MarketDataView />);
    expect(await screen.findByText("Market Data")).toBeTruthy();
    expect(screen.getByText("Catalog")).toBeTruthy();
    expect(screen.getByText("Live Coverage")).toBeTruthy();
    // Catalog section shows segment state.
    expect(await screen.findByText(/NSE_FO/)).toBeTruthy();
  });

  it("opens Live Coverage directly via ?tab=coverage", async () => {
    primeCatalogMocks();
    renderAt(<MarketDataView />, "/market-data?tab=coverage");
    expect(await screen.findByText(/F&O Live Coverage/)).toBeTruthy();
    expect(await screen.findByText(/routed \/ /)).toBeTruthy();
    // Provider allocation visible.
    expect(await screen.findByText("Upstox")).toBeTruthy();
    expect(await screen.findByText("Fyers")).toBeTruthy();
  });

  it("applies coverage via the coverage endpoint", async () => {
    primeCatalogMocks();
    api.applyFnoCoverage.mockResolvedValue({
      status: "ok",
      routed: 432,
      by_provider: { upstox: 312, fyers: 120 },
    });
    renderAt(<MarketDataView />, "/market-data?tab=coverage");
    fireEvent.click(await screen.findByText("Apply coverage"));
    await waitFor(() => expect(api.applyFnoCoverage).toHaveBeenCalled());
    expect(await screen.findByText(/Applied/)).toBeTruthy();
  });

  it("surfaces coverage load errors honestly", async () => {
    primeCatalogMocks();
    api.getFnoCoverage.mockRejectedValue(new Error("coverage down"));
    renderAt(<MarketDataView />, "/market-data?tab=coverage");
    expect(await screen.findByText(/Coverage unavailable/)).toBeTruthy();
  });
});
