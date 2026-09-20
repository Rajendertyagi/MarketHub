import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "@/app/App";

vi.mock("@/api/market", () => ({
  getHistory: vi.fn(),
  listScanners: vi.fn(),
  runScanner: vi.fn(),
  searchInstruments: vi.fn(),
  resolveInstrument: vi.fn(),
}));

import { getHistory, listScanners, runScanner } from "@/api/market";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.location.hash = "";
});

describe("Router deep-link behavior", () => {
  it("deep-links to Charts with an exact instrument from the URL hash", async () => {
    (getHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      candles: [
        {
          timestamp: "2024-01-01T00:00:00Z",
          open: 1,
          high: 2,
          low: 0,
          close: 1.5,
        },
      ],
    });
    window.location.hash = "#/charts?key=NSE_HDFC&sym=HDFCBANK&ex=NSE&type=EQUITY";
    render(<App />);
    await waitFor(() => expect(screen.getByText(/HDFCBANK/)).toBeInTheDocument());
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

  it("redirects the index route to Charts", async () => {
    (getHistory as ReturnType<typeof vi.fn>).mockResolvedValue({ candles: [] });
    window.location.hash = "#/";
    render(<App />);
    await waitFor(() => expect(screen.getByText(/Historical Charts/)).toBeInTheDocument());
  });
});
