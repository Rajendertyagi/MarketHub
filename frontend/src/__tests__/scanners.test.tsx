import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/market", () => ({
  getHistory: vi.fn(),
  listScanners: vi.fn(),
  runScanner: vi.fn(),
  searchInstruments: vi.fn(),
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

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ScannersView", () => {
  it("renders the scanner list and result rows", async () => {
    (listScanners as ReturnType<typeof vi.fn>).mockResolvedValue(DEFS);
    (runScanner as ReturnType<typeof vi.fn>).mockResolvedValue({
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
    });

    renderWithProviders(<ScannersView />);
    expect(await screen.findByText("RELIANCE")).toBeInTheDocument();
  });

  it("shows derivative (option) controls only for option scanners", async () => {
    (listScanners as ReturnType<typeof vi.fn>).mockResolvedValue(DEFS);
    (runScanner as ReturnType<typeof vi.fn>).mockResolvedValue({
      scanner: "option_iv",
      universe: "FNO",
      as_of: null,
      eligible: 0,
      quoted: 0,
      matched: 0,
      rows: [],
    });

    renderWithProviders(<ScannersView />);
    // Default selection is the first scanner (equity) -> option controls hidden.
    expect(screen.queryByText("Expiry")).not.toBeInTheDocument();

    // Switch to the Option IV scanner.
    const select = (await screen.findByDisplayValue("Top Gainers")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "option_iv" } });

    await waitFor(() => expect(screen.getByText("Expiry")).toBeInTheDocument());
    expect(screen.getByText("ATM Range")).toBeInTheDocument();
    expect(screen.getByText("CE/PE")).toBeInTheDocument();
  });

  it("displays canonical IV fraction as a percentage", async () => {
    (listScanners as ReturnType<typeof vi.fn>).mockResolvedValue(DEFS);
    (runScanner as ReturnType<typeof vi.fn>).mockResolvedValue({
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
    });

    renderWithProviders(<ScannersView />);
    const select = (await screen.findByDisplayValue("Top Gainers")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "option_iv" } });
    expect(await screen.findByText("17.58%")).toBeInTheDocument();
  });

  it("preserves exact instrument identity when navigating to Charts", async () => {
    (listScanners as ReturnType<typeof vi.fn>).mockResolvedValue(DEFS);
    (runScanner as ReturnType<typeof vi.fn>).mockResolvedValue({
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
    });
    (resolveInstrument as ReturnType<typeof vi.fn>).mockResolvedValue({
      instrument_token: "OPT_NIFTY_CE",
      exchange: "NFO",
      tradingsymbol: "NIFTY24xxxCE",
      instrument_type: "OPTION",
    });

    renderWithProviders(<ScannersView />);
    const select = (await screen.findByDisplayValue("Top Gainers")) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "option_iv" } });
    const row = await screen.findByText("NIFTY24xxxCE");
    const rowEl = row.closest("tr");
    if (!rowEl) throw new Error("expected scanner row");
    fireEvent.click(rowEl);

    await waitFor(() => expect(resolveInstrument).toHaveBeenCalledWith("NIFTY24xxxCE", "OPTION"));
    expect(mockNavigate).toHaveBeenCalledWith(expect.stringContaining("key=OPT_NIFTY_CE"));
    expect(mockNavigate).toHaveBeenCalledWith(expect.stringContaining("type=OPTION"));
  });
});
