import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/market", () => ({
  getHistory: vi.fn(),
  listScanners: vi.fn(),
  runScanner: vi.fn(),
  resolveInstrument: vi.fn(),
}));

import type * as RR from "react-router-dom";
import { listScanners, resolveInstrument, runScanner } from "@/api/market";
import { ScannersView } from "@/features/scanners/ScannersView";

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = (await importOriginal()) as typeof RR;
  return { ...actual, useNavigate: () => mockNavigate };
});

function renderWithProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/scanners"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

const DEFS = [
  {
    name: "top_gainers",
    title: "Top Gainers",
    instrument_class: "equity",
    metric: "change_percent",
    order: "desc",
    contract_kind: null,
    extra: null,
    description: "Equity gainers",
  },
  {
    name: "option_iv",
    title: "Option IV",
    instrument_class: "option",
    metric: "iv",
    order: "desc",
    contract_kind: "option",
    extra: ["expiry", "atm_range", "option_type"],
    description: "Option IV",
  },
];

function mockRunScannerByName(results: Record<string, unknown>) {
  (runScanner as ReturnType<typeof vi.fn>).mockImplementation((name: string) => {
    const r = results[name];
    if (r) return Promise.resolve(r);
    return Promise.resolve({
      scanner: name,
      universe: "FNO",
      as_of: null,
      eligible: 0,
      quoted: 0,
      matched: 0,
      rows: [],
    });
  });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ScannersView", () => {
  it("renders scanner cards and result rows", async () => {
    (listScanners as ReturnType<typeof vi.fn>).mockResolvedValue(DEFS);
    mockRunScannerByName({
      top_gainers: {
        scanner: "top_gainers",
        universe: "FNO",
        as_of: null,
        eligible: 1,
        quoted: 1,
        matched: 1,
        rows: [
          {
            symbol: "RELIANCE",
            sector: "ENERGY",
            ltp: 2500,
            change: 10,
            change_percent: 0.4,
            volume: 1000,
            oi: null,
            iv: null,
            status: "ok",
            freshness: "1s",
          },
        ],
      },
    });

    renderWithProviders(<ScannersView />);
    // Card grid renders scanner titles; result row/summary shows the symbol.
    expect((await screen.findAllByText("Top Gainers")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Option IV").length).toBeGreaterThan(0);
    expect((await screen.findAllByText("RELIANCE")).length).toBeGreaterThan(0);
  });

  it("shows derivative controls when option scanners are present", async () => {
    (listScanners as ReturnType<typeof vi.fn>).mockResolvedValue(DEFS);
    mockRunScannerByName({});

    renderWithProviders(<ScannersView />);
    await screen.findByText("Top Gainers");
    // Global option filters are shown when any option scanner is loaded.
    expect(screen.getByText("Expiry")).toBeInTheDocument();
    expect(screen.getByText("ATM Range")).toBeInTheDocument();
    expect(screen.getByText("CE/PE")).toBeInTheDocument();
  });

  it("hides derivative controls when no option scanners are loaded", async () => {
    (listScanners as ReturnType<typeof vi.fn>).mockResolvedValue([DEFS[0]]);
    mockRunScannerByName({});

    renderWithProviders(<ScannersView />);
    await screen.findByText("Top Gainers");
    expect(screen.queryByText("Expiry")).not.toBeInTheDocument();
    expect(screen.queryByText("ATM Range")).not.toBeInTheDocument();
    expect(screen.queryByText("CE/PE")).not.toBeInTheDocument();
  });

  it("displays canonical IV fraction as a percentage", async () => {
    (listScanners as ReturnType<typeof vi.fn>).mockResolvedValue(DEFS);
    mockRunScannerByName({
      option_iv: {
        scanner: "option_iv",
        universe: "FNO",
        as_of: null,
        eligible: 1,
        quoted: 1,
        matched: 1,
        rows: [
          {
            symbol: "NIFTY",
            sector: "",
            ltp: 100,
            change: null,
            change_percent: null,
            volume: null,
            oi: 10,
            iv: 0.1758,
            status: "ok",
            freshness: null,
            contract: "NIFTY24xxxCE",
            expiry: "2024-01-25",
            option_type: "CE",
            strike: 22000,
          },
        ],
      },
    });

    renderWithProviders(<ScannersView />);
    const toggle = await screen.findByRole("button", { name: /Option IV/i });
    fireEvent.click(toggle);
    expect(await screen.findByText("17.58%")).toBeInTheDocument();
  });

  it("preserves exact instrument identity when navigating to Charts", async () => {
    (listScanners as ReturnType<typeof vi.fn>).mockResolvedValue(DEFS);
    mockRunScannerByName({
      option_iv: {
        scanner: "option_iv",
        universe: "FNO",
        as_of: null,
        eligible: 1,
        quoted: 1,
        matched: 1,
        rows: [
          {
            symbol: "NIFTY",
            sector: "",
            ltp: 100,
            change: null,
            change_percent: null,
            volume: null,
            oi: 10,
            iv: 0.2,
            status: "ok",
            freshness: null,
            contract: "NIFTY24xxxCE",
            expiry: "2024-01-25",
            option_type: "CE",
            strike: 22000,
          },
        ],
      },
    });
    (resolveInstrument as ReturnType<typeof vi.fn>).mockResolvedValue({
      instrument_token: "OPT_NIFTY_CE",
      exchange: "NFO",
      tradingsymbol: "NIFTY24xxxCE",
      instrument_type: "OPTION",
    });

    renderWithProviders(<ScannersView />);
    const toggle = await screen.findByRole("button", { name: /Option IV/i });
    fireEvent.click(toggle);
    const row = await screen.findByText("NIFTY24xxxCE");
    const rowEl = row.closest("tr");
    if (!rowEl) throw new Error("expected scanner row");
    fireEvent.click(rowEl);

    await waitFor(() => expect(resolveInstrument).toHaveBeenCalledWith("NIFTY24xxxCE", "OPTION"));
    expect(mockNavigate).toHaveBeenCalledWith(expect.stringContaining("key=OPT_NIFTY_CE"));
    expect(mockNavigate).toHaveBeenCalledWith(expect.stringContaining("type=OPTION"));
  });
});
