import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

// jsdom has no EventSource; provide a no-op so the live-stream hook mounts.
beforeAll(() => {
  class MockEventSource {
    onopen: (() => void) | null = null;
    onmessage: ((e: { data: string }) => void) | null = null;
    onerror: (() => void) | null = null;
    readonly url: string;
    close() {}
    constructor(url: string) {
      this.url = url;
    }
  }
  (globalThis as unknown as { EventSource: unknown }).EventSource = MockEventSource;
});

const api = vi.hoisted(() => ({
  getLogs: vi.fn(),
}));
vi.mock("@/features/logs/api", () => ({
  getLogs: api.getLogs,
}));

import { LogsView } from "@/features/logs";

const RECORD = {
  ts: "2026-01-01T10:00:00Z",
  level: "INFO",
  logger: "app.alerts",
  message: "alert evaluated",
};

function renderWithProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/logs"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LogsView", () => {
  it("renders log records with level + component", async () => {
    api.getLogs.mockResolvedValue({
      status: "ok",
      count: 1,
      records: [RECORD],
    });
    renderWithProviders(<LogsView />);
    expect(await screen.findByText("Logs")).toBeTruthy();
    expect(await screen.findByText("alert evaluated")).toBeTruthy();
    expect(screen.getByText("app.alerts")).toBeTruthy();
    expect(screen.getByText("INFO", { selector: "td" })).toBeTruthy();
  });

  it("applies level filter on Apply", async () => {
    api.getLogs.mockResolvedValue({ status: "ok", count: 0, records: [] });
    renderWithProviders(<LogsView />);
    await screen.findByText("Logs");
    // The level <select> is the only combobox on the page.
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "ERROR" } });
    fireEvent.click(screen.getByText("Apply"));
    await waitFor(() =>
      expect(api.getLogs).toHaveBeenLastCalledWith(
        expect.objectContaining({ level: "ERROR" }),
        expect.anything(),
      ),
    );
  });

  it("shows error state on load failure", async () => {
    api.getLogs.mockRejectedValue(new Error("logs load failed"));
    renderWithProviders(<LogsView />);
    expect(await screen.findByText(/logs load failed/)).toBeTruthy();
  });
});
