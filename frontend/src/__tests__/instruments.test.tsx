import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getSegments: vi.fn(),
  getSyncState: vi.fn(),
  setSegments: vi.fn(),
  syncInstruments: vi.fn(),
}));
vi.mock("@/api/market", () => ({
  getSegments: api.getSegments,
  getSyncState: api.getSyncState,
  setSegments: api.setSegments,
  syncInstruments: api.syncInstruments,
}));

import { InstrumentsView } from "@/features/instruments/InstrumentsView";

const SEGMENTS = [
  { segment: "NSE_EQ", enabled: true, default: true, catalog_rows: 1800 },
  { segment: "NSE_FO", enabled: true, default: true, catalog_rows: 320 },
  { segment: "NSE_INDEX", enabled: true, default: true, catalog_rows: 50 },
  { segment: "BSE_INDEX", enabled: true, default: true, catalog_rows: 30 },
  { segment: "BSE_EQ", enabled: false, default: false, catalog_rows: 0 },
];

function segResponse(enabled: string[], segments = SEGMENTS) {
  return {
    status: "ok",
    default: ["NSE_EQ", "NSE_FO", "NSE_INDEX", "BSE_INDEX"],
    enabled,
    segments,
  };
}

function renderWithProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/instruments"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function segmentCheckbox(label: string): HTMLInputElement {
  const el = screen.getByText(new RegExp(`^${label}`)) as HTMLElement;
  const row = el.closest(".segment-item") as Element;
  return row.querySelector("input[type=checkbox]") as HTMLInputElement;
}

describe("InstrumentsView", () => {
  it("renders catalog/source status and segment states", async () => {
    api.getSegments.mockResolvedValue(segResponse(["NSE_EQ", "NSE_FO", "NSE_INDEX", "BSE_INDEX"]));
    api.getSyncState.mockResolvedValue({
      providers: [
        { provider: "upstox", status: "ready", last_sync: "2026-01-01", rows: 2200, error: null },
      ],
    });
    renderWithProviders(<InstrumentsView />);
    expect(await screen.findByText("Source / Sync status")).toBeTruthy();
    expect(await screen.findByText("Segments")).toBeTruthy();
    // Segment labels render.
    for (const s of ["NSE_EQ", "NSE_FO", "NSE_INDEX", "BSE_INDEX", "BSE_EQ"]) {
      expect(screen.getByText(new RegExp(`^${s}`))).toBeTruthy();
    }
    // Provider sync status row renders.
    expect(screen.getByText("upstox")).toBeTruthy();
  });

  it("reflects SAVED state, not defaults (Fyers NSE_EQ off + NSE_INDEX on)", async () => {
    // Backend returns NSE_EQ disabled but NSE_INDEX enabled — React must show
    // that exact saved state, not overwrite it with defaults.
    api.getSegments.mockResolvedValue(segResponse(["NSE_FO", "NSE_INDEX", "BSE_INDEX"]));
    api.getSyncState.mockResolvedValue({ providers: [] });
    renderWithProviders(<InstrumentsView />);
    await screen.findByText("Segments");
    await waitFor(() => {
      expect(segmentCheckbox("NSE_EQ").checked).toBe(false);
    });
    expect(segmentCheckbox("NSE_INDEX").checked).toBe(true);
    expect(segmentCheckbox("NSE_FO").checked).toBe(true);
  });

  it("toggles a segment and saves via Save & Re-sync", async () => {
    api.getSegments.mockResolvedValue(segResponse(["NSE_EQ", "NSE_FO", "NSE_INDEX", "BSE_INDEX"]));
    api.getSyncState.mockResolvedValue({ providers: [] });
    api.setSegments.mockResolvedValue({ status: "ok", enabled: [] });
    api.syncInstruments.mockResolvedValue({ status: "ok", rows: 2200 });
    renderWithProviders(<InstrumentsView />);
    await screen.findByText("Segments");
    // Turn NSE_EQ off.
    const eq = segmentCheckbox("NSE_EQ");
    await waitFor(() => expect(eq.checked).toBe(true));
    fireEvent.click(eq);
    fireEvent.click(screen.getByText(/Save & Re-sync/));
    await waitFor(() => expect(api.setSegments).toHaveBeenCalled());
    const saved = api.setSegments.mock.calls[0]?.[0] as string[];
    expect(saved).not.toContain("NSE_EQ");
    expect(saved).toContain("NSE_INDEX");
    await waitFor(() => expect(api.syncInstruments).toHaveBeenCalledWith("upstox"));
    expect(await screen.findByText(/re-sync ok/)).toBeTruthy();
  });

  it("shows sync failure", async () => {
    api.getSegments.mockResolvedValue(segResponse(["NSE_EQ", "NSE_FO", "NSE_INDEX", "BSE_INDEX"]));
    api.getSyncState.mockResolvedValue({ providers: [] });
    api.setSegments.mockResolvedValue({ status: "ok", enabled: [] });
    api.syncInstruments.mockRejectedValue(new Error("sync failed: UpstreamError"));
    renderWithProviders(<InstrumentsView />);
    await screen.findByText("Segments");
    fireEvent.click(screen.getByText(/Save & Re-sync/));
    expect(await screen.findByText(/sync failed: UpstreamError/)).toBeTruthy();
  });
});
