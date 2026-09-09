import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/app/ThemeProvider";
import { ApiError } from "@/types";

vi.mock("@/api/market", () => ({
  getHistory: vi.fn(),
  listScanners: vi.fn(),
  runScanner: vi.fn(),
  searchInstruments: vi.fn(),
  resolveInstrument: vi.fn(),
}));

import { getHistory } from "@/api/market";
import { ChartsView } from "@/features/charts/ChartsView";

function renderWithProviders(ui: ReactNode, entries: string[] = ["/charts"]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <MemoryRouter initialEntries={entries}>{ui}</MemoryRouter>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

const URL_WITH_INST =
  "/charts?key=NSE_HDFC&sym=HDFCBANK&ex=NSE&type=EQUITY";

afterEach(() => cleanup());

describe("ChartsView async states", () => {
  it("shows loading state while history is requested", async () => {
    (getHistory as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise(() => {}),
    );
    renderWithProviders(<ChartsView />, [URL_WITH_INST]);
    expect(await screen.findByText(/Loading history/)).toBeInTheDocument();
  });

  it("shows empty state when no candles returned", async () => {
    (getHistory as ReturnType<typeof vi.fn>).mockResolvedValue({
      candles: [],
    });
    renderWithProviders(<ChartsView />, [URL_WITH_INST]);
    expect(
      await screen.findByText(/No history data returned/),
    ).toBeInTheDocument();
  });

  it("shows error state on backend failure", async () => {
    (getHistory as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError("http", "history fetch failed", { status: 502 }),
    );
    renderWithProviders(<ChartsView />, [URL_WITH_INST]);
    expect(await screen.findByText(/history fetch failed/)).toBeInTheDocument();
  });

  it("shows unsupported-provider state for unsupported provider", async () => {
    (getHistory as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError("http", "Provider does not support history for this instrument", {
        status: 400,
      }),
    );
    renderWithProviders(<ChartsView />, [URL_WITH_INST]);
    expect(
      await screen.findByText(/does not support history/),
    ).toBeInTheDocument();
  });

  it("renders the chart heading and instrument context from URL", async () => {
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
    renderWithProviders(<ChartsView />, [URL_WITH_INST]);
    await waitFor(() =>
      expect(screen.getByText(/HDFCBANK/)).toBeInTheDocument(),
    );
  });
});
