import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FnoWorkspaceView } from "@/features/fno/FnoWorkspaceView";

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

function renderWithProviders(ui: ReactNode, entries: string[]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={entries}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

const EQUITY_WORKSPACE = {
  status: "ok",
  symbol: "HDFCBANK",
  equity_key: "NSE:123",
  spot_quote: { ltp: 2500, change: 10, change_percent: 0.4 },
  futures: [
    {
      key: "NSE_FO:1",
      label: "HDFCBANK26OCTFUT",
      expiry: "2026-10-29",
      provider: "upstox",
      quote: { ltp: 2505, best_bid: 2504, best_ask: 2506 },
    },
  ],
  options: [
    {
      key: "NSE_FO:2",
      label: "HDFCBANK26OCT2500CE",
      expiry: "2026-10-29",
      strike: 2500,
      option_type: "CE",
      provider: "upstox",
      quote: {
        ltp: 50,
        bid: 49,
        ask: 51,
        open_interest: 1000,
        oi_change: 10,
        volume: 500,
        iv: 0.1758,
        delta: 0.5,
      },
    },
    {
      key: "NSE_FO:3",
      label: "HDFCBANK26OCT2500PE",
      expiry: "2026-10-29",
      strike: 2500,
      option_type: "PE",
      provider: "upstox",
      quote: {
        ltp: 40,
        bid: 39,
        ask: 41,
        open_interest: 800,
        oi_change: 5,
        volume: 400,
        iv: 0.18,
        delta: -0.5,
      },
    },
  ],
  option_expiries: ["2026-10-29", "2026-11-26"],
  selected_expiry: "2026-10-29",
  atm: 2500,
  notes: ["loaded nearest expiry"],
};

const INDEX_CHAIN = {
  underlying: "NIFTY",
  expiry: "2026-10-30",
  expiries_available: ["2026-10-30", "2026-11-27"],
  spot: 25000,
  spot_basis: "live",
  atm_strike: 25000,
  window: 10,
  strikes_loaded: 1,
  strikes_total_listed: 50,
  rows: [
    {
      strike: 25000,
      atm: true,
      call: {
        instrument_key: "NSE:1",
        symbol: "NIFTY25000CE",
        exchange: "NSE",
        option_type: "CE",
        strike: 25000,
        quote: { ltp: 100, iv: 0.18, delta: 0.5, open_interest: 1000 },
      },
      put: {
        instrument_key: "NSE:2",
        symbol: "NIFTY25000PE",
        exchange: "NSE",
        option_type: "PE",
        strike: 25000,
        quote: { ltp: 90, iv: 0.19, delta: -0.5, open_interest: 1100 },
      },
    },
  ],
  analytics: { scope: "loaded_window", pcr_by_oi: 1.1, total_call_oi: 1000, total_put_oi: 1100 },
};

function makeFetch() {
  return vi.fn(async (input: string | URL | Request) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (url.includes("/api/market/fno/universe"))
      return jsonResponse({
        status: "ok",
        count: 1,
        universe: [
          {
            symbol: "HDFCBANK",
            name: "HDFC Bank",
            equity_key: "NSE:123",
            futures_available: true,
            options_available: true,
            futures_count: 2,
            options_count: 10,
            future_expiries: 3,
            option_expiries: 3,
            nearest_future: "2026-10",
            nearest_option: "2026-10",
          },
        ],
      });
    if (url.includes("/api/options/index-underlyings"))
      return jsonResponse({
        underlyings: [
          { label: "NIFTY", exchange: "NSE" },
          { label: "BANKNIFTY", exchange: "NSE" },
          { label: "FINNIFTY", exchange: "NSE" },
          { label: "MIDCPNIFTY", exchange: "NSE" },
        ],
      });
    if (url.includes("/api/market/fno/view"))
      return jsonResponse({
        status: "ok",
        symbol: "HDFCBANK",
        active_view: {},
        resolved_count: 0,
        apply: {},
      });
    if (url.includes("/api/futures")) return jsonResponse({ underlying: "NIFTY", contracts: [] });
    if (url.includes("/api/market/fno/stock/")) return jsonResponse(EQUITY_WORKSPACE);
    if (url.includes("/api/options/chain/view")) return jsonResponse(INDEX_CHAIN);
    return jsonResponse({ error: "not found" }, 404);
  });
}

function firstOptionLink(): HTMLAnchorElement | undefined {
  return (Array.from(document.querySelectorAll("a.link")) as HTMLAnchorElement[]).find((a) =>
    (a.getAttribute("href") ?? "").includes("type=OPTION"),
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("FnoWorkspaceView", () => {
  it("renders an equity option chain with the bounded active-view subscription", async () => {
    const fetchMock = makeFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<FnoWorkspaceView />, ["/fno?sym=HDFCBANK&kind=equity&tab=chain"]);

    // Equity workspace establishes the bounded active-view subscription.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((c: unknown[]) => String(c[0]).includes("/api/market/fno/view")),
      ).toBe(true),
    );

    // ATM marker present for the 2500 strike row.
    expect((await screen.findAllByText("ATM")).length).toBeGreaterThan(0);
    // LTP link preserves exact option identity (type=OPTION, key=NSE_FO:2).
    const optLink = await waitFor(() => {
      const l = firstOptionLink();
      if (!l) throw new Error("expected option link");
      return l;
    });
    expect(optLink.getAttribute("href")).toContain("type=OPTION");
    expect(optLink.getAttribute("href")).toContain("key=NSE_FO%3A2");
  });

  it("shows the underlying picker and opens a selected equity workspace", async () => {
    const fetchMock = makeFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<FnoWorkspaceView />, ["/fno"]);

    expect(await screen.findByText("F&O Underlyings")).toBeTruthy();
    // Wait for the underlying list to load, then click the HDFCBANK equity row
    // (rows are clickable, no separate Open button).
    const row = (await screen.findByText("HDFCBANK")).closest("tr");
    if (!row) throw new Error("expected HDFCBANK row");
    fireEvent.click(row);

    // Workspace renders the Option Chain for the selected underlying.
    expect(await screen.findByText("Option Chain")).toBeTruthy();
  });

  it("renders an index option chain (link preserves identity)", async () => {
    const fetchMock = makeFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<FnoWorkspaceView />, ["/fno?sym=NIFTY&kind=index&tab=chain"]);

    // Bootstrap picks the first expiry from the chain response and the chain
    // renders with option links preserving the exact option identity.
    const optLink = await waitFor(() => {
      const l = firstOptionLink();
      if (!l) throw new Error("expected option link");
      return l;
    });
    expect(optLink.getAttribute("href")).toContain("key=NSE%3A1");
  });

  it("renders index option-chain analytics inline beneath the chain", async () => {
    const fetchMock = makeFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<FnoWorkspaceView />, ["/fno?sym=NIFTY&kind=index&tab=chain"]);

    // Analytics (PCR / ATM straddle) renders inline directly below the chain
    // table, not on a separate tab.
    expect(await screen.findByText("PCR (OI)")).toBeTruthy();
    expect(await screen.findByText("ATM Straddle")).toBeTruthy();
  });

  it("consumes backend-owned index option underlyings (no hard-coded list)", async () => {
    const fetchMock = makeFetch();
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<FnoWorkspaceView />, ["/fno"]);

    // The canonical index list is fetched from the backend endpoint, not
    // hard-coded in the frontend.
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((c: unknown[]) =>
          String(c[0]).includes("/api/options/index-underlyings"),
        ),
      ).toBe(true),
    );

    // Index labels from the backend appear in the picker.
    expect(await screen.findByText("NIFTY")).toBeTruthy();
    expect(await screen.findByText("BANKNIFTY")).toBeTruthy();
    expect(await screen.findByText("FINNIFTY")).toBeTruthy();
    expect(await screen.findByText("MIDCPNIFTY")).toBeTruthy();
  });
});
