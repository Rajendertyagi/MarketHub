import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FnoWorkspaceView } from "@/features/fno/FnoWorkspaceView";

let viewCalls = 0;

vi.mock("@/api/market", async (orig) => {
  const actual = (await orig()) as Record<string, unknown>;
  return {
    ...actual,
    postFnoView: vi.fn(async () => {
      viewCalls += 1;
      return {
        status: "ok",
        symbol: "HDFCBANK",
        active_view: {},
        resolved_count: 0,
        apply: {},
      };
    }),
    getFnoUniverse: vi.fn(async () => ({
      universe: [
        { symbol: "HDFCBANK", name: "HDFC", kind: "equity" },
      ],
    })),
    getFnoWorkspace: vi.fn(async () => ({
      status: "ok",
      symbol: "HDFCBANK",
      equity_key: "NSE_EQ|INE002A01010",
      futures: [],
      option_expiries: ["2026-10-27"],
      selected_expiry: "2026-10-27",
      atm: 100,
      atm_basis: "spot",
      options: [],
      by_provider: {},
      notes: [],
    })),
    getFutures: vi.fn(async () => ({ underlying: "NIFTY", contracts: [] })),
    getOptionChainView: vi.fn(async () => ({
      underlying: "NIFTY",
      expiry: "2026-10-27",
      expiries_available: ["2026-10-27"],
      spot: 100,
      spot_basis: "spot",
      atm_strike: 100,
      window: 10,
      strikes_loaded: 0,
      strikes_total_listed: 0,
      rows: [],
      analytics: { scope: "window" },
    })),
    getIndexOptionUnderlyings: vi.fn(async () => []),
  };
});

describe("FnoWorkspaceView active-view request storm", () => {
  beforeEach(() => {
    viewCalls = 0;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("does not re-post the active view on every render", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/fno?sym=HDFCBANK&kind=equity&window=10"]}>
          <FnoWorkspaceView />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    // The active view is established ONCE for (kind, sym, window).
    await waitFor(() => expect(viewCalls).toBeGreaterThan(0), { timeout: 5000 });
    const settled = viewCalls;
    // Give any render/mutation feedback loop time to show itself.
    await new Promise((r) => setTimeout(r, 1500));
    // A render loop would post far more than a handful of times.
    expect(viewCalls).toBeLessThanOrEqual(settled + 2);
  });
});
