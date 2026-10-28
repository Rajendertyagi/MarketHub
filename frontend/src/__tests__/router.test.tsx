import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "@/app/App";

vi.mock("@/api/market", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/market")>();
  return {
    ...actual,
    getHistory: vi.fn(),
    listScanners: vi.fn(),
    runScanner: vi.fn(),
    resolveMarketInstrument: vi.fn(),
  };
});

// The Vela workspace needs a real rendering host; these route tests exercise
// identity hydration, so stand in for the chart itself.
vi.mock("@/features/chart/VelaChart", () => ({
  VelaChart: ({ instrument }: { instrument: { tradingsymbol: string } }) => (
    <div>{`chart:${instrument.tradingsymbol}`}</div>
  ),
}));

vi.mock("@/features/dashboard", () => ({
  DashboardView: () => <div>Dashboard home</div>,
}));

import { listScanners, resolveMarketInstrument, runScanner } from "@/api/market";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.location.hash = "";
});

describe("Router deep-link behavior", () => {
  it("deep-links to the chart with an exact instrument from the URL hash", async () => {
    window.location.hash = "#/chart?key=NSE_HDFC&sym=HDFCBANK&ex=NSE&type=EQUITY";
    render(<App />);
    expect(await screen.findByText("chart:HDFCBANK")).toBeInTheDocument();
    // Exact identity in the URL must not be re-resolved through the catalogue.
    expect(resolveMarketInstrument).not.toHaveBeenCalled();
  });

  it("resolves a symbol-only chart link through the canonical exact resolver", async () => {
    (resolveMarketInstrument as ReturnType<typeof vi.fn>).mockResolvedValue({
      instrument_key: "NSE:NSE:NSE:HDFCBANK-EQ",
      provider: "fyers",
      provider_token: "NSE:NSE:HDFCBANK-EQ",
      symbol: "HDFCBANK",
      exchange: "NSE",
      type: "EQUITY",
    });
    window.location.hash = "#/chart?sym=HDFCBANK&ex=NSE&type=EQUITY";
    render(<App />);
    expect(await screen.findByText("chart:HDFCBANK")).toBeInTheDocument();
    expect(resolveMarketInstrument).toHaveBeenCalledWith(
      "HDFCBANK",
      expect.objectContaining({ exchange: "NSE", type: "EQUITY" }),
      expect.anything(),
    );
  });

  it("deep-links to Scanners", async () => {
    (listScanners as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        name: "top_gainers",
        title: "Top Gainers",
        instrument_class: "equity",
        metric: "change_percent",
        order: "desc",
        contract_kind: null,
        extra: null,
        description: "",
      },
    ]);
    (runScanner as ReturnType<typeof vi.fn>).mockResolvedValue({
      scanner: "top_gainers",
      universe: "FNO",
      as_of: null,
      eligible: 0,
      quoted: 0,
      matched: 0,
      rows: [],
    });
    window.location.hash = "#/scanners";
    render(<App />);
    await waitFor(() =>
      expect(screen.findByRole("heading", { name: /Scanners/ })).resolves.toBeInTheDocument(),
    );
  });

  it("redirects the index route to the dashboard", async () => {
    window.location.hash = "#/";
    render(<App />);
    expect(await screen.findByText("Dashboard home")).toBeInTheDocument();
  });
});
