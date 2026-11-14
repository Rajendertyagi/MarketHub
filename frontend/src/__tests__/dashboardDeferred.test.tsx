import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

type IORecord = {
  observe: ReturnType<typeof vi.fn>;
  unobserve: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
  callback: IntersectionObserverCallback;
  options?: IntersectionObserverInit;
};

const originalIO = globalThis.IntersectionObserver;

function installObserverMock(mode: "none" | "intersect") {
  const instances: IORecord[] = [];
  class MockObserver {
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
    takeRecords = vi.fn(() => []);
    callback: IntersectionObserverCallback;
    options?: IntersectionObserverInit;
    constructor(cb: IntersectionObserverCallback, options?: IntersectionObserverInit) {
      this.callback = cb;
      this.options = options;
      instances.push({
        observe: this.observe,
        unobserve: this.unobserve,
        disconnect: this.disconnect,
        callback: cb,
        options,
      });
      if (mode === "intersect") {
        this.observe.mockImplementation((target: Element) => {
          cb(
            [{ isIntersecting: true, target } as IntersectionObserverEntry],
            this as unknown as IntersectionObserver,
          );
        });
      }
    }
  }
  globalThis.IntersectionObserver = MockObserver as unknown as typeof IntersectionObserver;
  return instances;
}

async function loadDashboard() {
  vi.resetModules();

  vi.doMock("@/api/market", () => ({
    getSectorHeatmap: vi.fn(),
    getBreadth: vi.fn(),
    getFnoUniverse: vi.fn(),
    runScanner: vi.fn(),
    getSubscriptions: vi.fn(),
  }));

  vi.doMock("@/features/dashboard/api", () => ({
    getMarketQuotes: vi.fn(),
  }));

  vi.doMock("@/streams/useStream", () => ({
    useStream: vi.fn(),
  }));

  const market = await import("@/api/market");
  const dashApi = await import("@/features/dashboard/api");
  const { DashboardView } = await import("@/features/dashboard/DashboardView");

  (dashApi.getMarketQuotes as ReturnType<typeof vi.fn>).mockResolvedValue({ quotes: [] });
  (market.getSubscriptions as ReturnType<typeof vi.fn>).mockResolvedValue({ indices: [] });
  (market.getSectorHeatmap as ReturnType<typeof vi.fn>).mockResolvedValue({
    universe: "NIFTY50",
    sector_count: 0,
    sectors: [],
  });
  (market.getBreadth as ReturnType<typeof vi.fn>).mockResolvedValue({
    universe: "NIFTY50",
    advances: 0,
    declines: 0,
    unchanged: 0,
    advance_percent: 0,
  });
  (market.getFnoUniverse as ReturnType<typeof vi.fn>).mockResolvedValue({ count: 0, universe: [] });
  (market.runScanner as ReturnType<typeof vi.fn>).mockResolvedValue({
    scanner: "gainers",
    universe: "FNO",
    as_of: null,
    eligible: 0,
    quoted: 0,
    matched: 0,
    rows: [],
  });

  function renderDashboard(ui = <DashboardView />) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    return render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/"]}>{ui}</MemoryRouter>
      </QueryClientProvider>,
    );
  }

  return { market, dashApi, DashboardView, renderDashboard };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  globalThis.IntersectionObserver = originalIO;
});

describe("DashboardView secondary widget deferral", () => {
  it("keeps critical dashboard requests immediate and defers secondary widget fetches until visible", async () => {
    installObserverMock("none");
    const { market, dashApi, renderDashboard } = await loadDashboard();
    renderDashboard();

    expect(screen.getByRole("status")).toHaveTextContent("Reconnecting");
    expect(screen.getByLabelText("Major indices")).toBeInTheDocument();
    expect(screen.getByLabelText("Market detail")).toBeInTheDocument();

    await waitFor(() => {
      expect(dashApi.getMarketQuotes).toHaveBeenCalled();
    });
    expect(market.getSubscriptions).toHaveBeenCalled();

    expect(market.getSectorHeatmap).not.toHaveBeenCalled();
    expect(market.getBreadth).not.toHaveBeenCalled();
    expect(market.getFnoUniverse).not.toHaveBeenCalled();
    expect(market.runScanner).not.toHaveBeenCalled();
  });

  it("mounts and fetches each secondary widget once when visible, without duplicate requests", async () => {
    installObserverMock("intersect");
    const { market, renderDashboard } = await loadDashboard();
    renderDashboard();

    await waitFor(() => {
      expect(market.getSectorHeatmap).toHaveBeenCalledTimes(1);
      expect(market.getBreadth).toHaveBeenCalledTimes(1);
      expect(market.getFnoUniverse).toHaveBeenCalledTimes(1);
      expect(market.runScanner).toHaveBeenCalledTimes(1);
    });

    await waitFor(() => {
      expect(market.getSectorHeatmap).toHaveBeenCalledTimes(1);
      expect(market.getBreadth).toHaveBeenCalledTimes(1);
      expect(market.getFnoUniverse).toHaveBeenCalledTimes(1);
      expect(market.runScanner).toHaveBeenCalledTimes(1);
    });
  });

  it("does not recreate observers on repeated dashboard rerenders and activates each widget only once", async () => {
    const instances = installObserverMock("none");
    const { market, DashboardView, renderDashboard } = await loadDashboard();
    const view = renderDashboard();

    expect(instances).toHaveLength(4);
    expect(instances.every((i) => i.observe.mock.calls.length === 1)).toBe(true);

    view.rerender(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}
      >
        <MemoryRouter initialEntries={["/"]}>
          <DashboardView />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    view.rerender(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })}
      >
        <MemoryRouter initialEntries={["/"]}>
          <DashboardView />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(instances).toHaveLength(4);
    expect(instances.every((i) => i.observe.mock.calls.length === 1)).toBe(true);
    expect(instances.every((i) => i.disconnect.mock.calls.length === 0)).toBe(true);

    await act(async () => {
      for (const inst of instances) {
        const target = inst.observe.mock.calls[0]?.[0] as Element;
        inst.callback(
          [{ isIntersecting: true, target } as IntersectionObserverEntry],
          {} as IntersectionObserver,
        );
      }
    });

    await waitFor(() => {
      expect(market.getSectorHeatmap).toHaveBeenCalledTimes(1);
      expect(market.getBreadth).toHaveBeenCalledTimes(1);
      expect(market.getFnoUniverse).toHaveBeenCalledTimes(1);
      expect(market.runScanner).toHaveBeenCalledTimes(1);
    });
    expect(instances.every((i) => i.disconnect.mock.calls.length >= 1)).toBe(true);
  });

  it("cleans up observers on unmount", async () => {
    const instances = installObserverMock("none");
    const { renderDashboard } = await loadDashboard();
    const view = renderDashboard();
    expect(instances).toHaveLength(4);
    view.unmount();
    expect(instances.every((i) => i.disconnect.mock.calls.length === 1)).toBe(true);
  });

  it("falls back to visible when IntersectionObserver is unavailable", async () => {
    // @ts-expect-error - simulate unsupported environment
    delete globalThis.IntersectionObserver;
    const { market, dashApi, renderDashboard } = await loadDashboard();
    renderDashboard();

    await waitFor(() => {
      expect(dashApi.getMarketQuotes).toHaveBeenCalled();
    });
    expect(market.getSubscriptions).toHaveBeenCalled();
    await waitFor(() => {
      expect(market.getSectorHeatmap).toHaveBeenCalledTimes(1);
      expect(market.getBreadth).toHaveBeenCalledTimes(1);
      expect(market.getFnoUniverse).toHaveBeenCalledTimes(1);
      expect(market.runScanner).toHaveBeenCalledTimes(1);
    });
  });
});
