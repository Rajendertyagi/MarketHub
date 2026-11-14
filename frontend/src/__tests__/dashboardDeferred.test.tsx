import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/market", () => ({
  getSectorHeatmap: vi.fn(),
  getBreadth: vi.fn(),
  getFnoUniverse: vi.fn(),
  runScanner: vi.fn(),
  getSubscriptions: vi.fn(),
}));

vi.mock("@/features/dashboard/api", () => ({
  getMarketQuotes: vi.fn(),
}));

vi.mock("@/streams/useStream", () => ({
  useStream: vi.fn(),
}));

import {
  getBreadth,
  getFnoUniverse,
  getSectorHeatmap,
  getSubscriptions,
  runScanner,
} from "@/api/market";
import { getMarketQuotes } from "@/features/dashboard/api";
import { DashboardView } from "@/features/dashboard/DashboardView";

function renderDashboard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/"]}>
        <DashboardView />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

class NonIntersectingObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

class IntersectingObserver {
  private cb: IntersectionObserverCallback;
  constructor(cb: IntersectionObserverCallback) {
    this.cb = cb;
  }
  observe(target: Element) {
    this.cb(
      [{ isIntersecting: true, target } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    );
  }
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

const originalIO = globalThis.IntersectionObserver;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  globalThis.IntersectionObserver = originalIO;
});

beforeEach(() => {
  (getMarketQuotes as ReturnType<typeof vi.fn>).mockResolvedValue({ quotes: [] });
  (getSubscriptions as ReturnType<typeof vi.fn>).mockResolvedValue({ indices: [] });
  (getSectorHeatmap as ReturnType<typeof vi.fn>).mockResolvedValue({
    universe: "NIFTY50",
    sector_count: 0,
    sectors: [],
  });
  (getBreadth as ReturnType<typeof vi.fn>).mockResolvedValue({
    universe: "NIFTY50",
    advances: 0,
    declines: 0,
    unchanged: 0,
    advance_percent: 0,
  });
  (getFnoUniverse as ReturnType<typeof vi.fn>).mockResolvedValue({ count: 0, universe: [] });
  (runScanner as ReturnType<typeof vi.fn>).mockResolvedValue({
    scanner: "gainers",
    universe: "FNO",
    as_of: null,
    eligible: 0,
    quoted: 0,
    matched: 0,
    rows: [],
  });
});

describe("DashboardView secondary widget deferral", () => {
  it("keeps critical dashboard shell immediate and defers secondary widget fetches until visible", async () => {
    globalThis.IntersectionObserver =
      NonIntersectingObserver as unknown as typeof IntersectionObserver;

    renderDashboard();

    // Critical shell/hero render immediately.
    expect(screen.getByRole("status")).toHaveTextContent("Reconnecting");
    expect(screen.getByLabelText("Major indices")).toBeInTheDocument();
    expect(screen.getByLabelText("Market detail")).toBeInTheDocument();

    await waitFor(() => {
      expect(getMarketQuotes).toHaveBeenCalled();
    });
    expect(getSubscriptions).toHaveBeenCalled();

    // Secondary widgets are not mounted/fetched while out of view.
    expect(getSectorHeatmap).not.toHaveBeenCalled();
    expect(getBreadth).not.toHaveBeenCalled();
    expect(getFnoUniverse).not.toHaveBeenCalled();
    expect(runScanner).not.toHaveBeenCalled();
  });

  it("mounts and fetches secondary widgets once they become visible", async () => {
    globalThis.IntersectionObserver =
      IntersectingObserver as unknown as typeof IntersectionObserver;

    renderDashboard();

    await waitFor(() => {
      expect(getSectorHeatmap).toHaveBeenCalled();
      expect(getBreadth).toHaveBeenCalled();
      expect(getFnoUniverse).toHaveBeenCalled();
      expect(runScanner).toHaveBeenCalled();
    });
  });
});
