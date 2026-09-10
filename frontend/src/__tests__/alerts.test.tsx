import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const api = vi.hoisted(() => ({
  getAlerts: vi.fn(),
  createAlert: vi.fn(),
  deleteAlert: vi.fn(),
  rearmAlert: vi.fn(),
  setAlertEnabled: vi.fn(),
  getAlertHistory: vi.fn(),
  clearAlertHistory: vi.fn(),
}));
vi.mock("@/features/alerts/api", () => ({
  getAlerts: api.getAlerts,
  createAlert: api.createAlert,
  deleteAlert: api.deleteAlert,
  rearmAlert: api.rearmAlert,
  setAlertEnabled: api.setAlertEnabled,
  getAlertHistory: api.getAlertHistory,
  clearAlertHistory: api.clearAlertHistory,
}));

import { AlertsView } from "@/features/alerts";

const ALERT = {
  id: 1,
  exchange: "NSE",
  instrument_token: "NSE_EQ|RELIANCE",
  tradingsymbol: "RELIANCE",
  field: "ltp",
  operator: "gt",
  threshold: 2500,
  enabled: 1,
  state: "inactive",
  created_at: "2026-01-01T00:00:00Z",
  triggered_at: null,
};
const NOTIFICATION = {
  alert_id: 1,
  tradingsymbol: "RELIANCE",
  field: "ltp",
  operator: "gt",
  threshold: 2500,
  value: 2600,
  ts: 1700000000,
};
const HISTORY_ROW = {
  id: 10,
  alert_id: 1,
  exchange: "NSE",
  instrument_token: "NSE_EQ|RELIANCE",
  tradingsymbol: "RELIANCE",
  field: "ltp",
  operator: "gt",
  threshold: 2500,
  observed_value: 2600,
  provider: "fyers",
  triggered_at: "2026-01-01T10:00:00Z",
  created_at: "2026-01-01T10:00:00Z",
};

function renderWithProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/alerts"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AlertsView", () => {
  it("renders configured alerts with condition + state", async () => {
    api.getAlerts.mockResolvedValue({ alerts: [ALERT], notifications: [] });
    api.getAlertHistory.mockResolvedValue({
      history: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
    renderWithProviders(<AlertsView />);
    expect(await screen.findByText("RELIANCE")).toBeTruthy();
    expect(
      screen.getByText(
        (content, element) =>
          element?.tagName === "TD" &&
          content.includes("Last Traded Price") &&
          content.includes("2500"),
      ),
    ).toBeTruthy();
    expect(screen.getByText("inactive")).toBeTruthy();
  });

  it("shows error state on load failure", async () => {
    api.getAlerts.mockRejectedValue(new Error("alerts load failed"));
    api.getAlertHistory.mockResolvedValue({
      history: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
    renderWithProviders(<AlertsView />);
    expect(await screen.findByText(/alerts load failed/)).toBeTruthy();
  });

  it("creates an alert with the exact backend contract", async () => {
    api.getAlerts.mockResolvedValue({ alerts: [], notifications: [] });
    api.getAlertHistory.mockResolvedValue({
      history: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
    api.createAlert.mockResolvedValue({ status: "ok" });
    renderWithProviders(<AlertsView />);
    await screen.findByText("New alert");
    fireEvent.change(screen.getByPlaceholderText("e.g. NSE_EQ|RELIANCE"), {
      target: { value: "NSE_EQ|RELIANCE" },
    });
    fireEvent.click(screen.getByText("Add alert"));
    await waitFor(() =>
      expect(api.createAlert).toHaveBeenCalledWith(
        expect.objectContaining({
          exchange: "NSE",
          instrument_token: "NSE_EQ|RELIANCE",
          tradingsymbol: "NSE_EQ|RELIANCE",
          field: "ltp",
          operator: "gt",
          threshold: 0,
        }),
      ),
    );
  });

  it("toggles enabled state through the mutation", async () => {
    api.getAlerts.mockResolvedValue({ alerts: [ALERT], notifications: [] });
    api.getAlertHistory.mockResolvedValue({
      history: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
    api.setAlertEnabled.mockResolvedValue({ status: "ok" });
    renderWithProviders(<AlertsView />);
    const toggle = await screen.findByRole("checkbox") as HTMLInputElement;
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(api.setAlertEnabled).toHaveBeenCalledWith(1, false),
    );
  });

  it("renders live trigger notifications", async () => {
    api.getAlerts.mockResolvedValue({
      alerts: [ALERT],
      notifications: [NOTIFICATION],
    });
    api.getAlertHistory.mockResolvedValue({
      history: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
    renderWithProviders(<AlertsView />);
    expect(await screen.findByText("Triggered")).toBeTruthy();
    expect(
      screen.getByText(
        (content, element) =>
          element?.tagName === "LI" &&
          content.includes("RELIANCE") &&
          content.includes("2600"),
      ),
    ).toBeTruthy();
  });

  it("renders trigger history rows", async () => {
    api.getAlerts.mockResolvedValue({ alerts: [], notifications: [] });
    api.getAlertHistory.mockResolvedValue({
      history: [HISTORY_ROW],
      total: 1,
      limit: 50,
      offset: 0,
    });
    renderWithProviders(<AlertsView />);
    expect(await screen.findByText("Trigger history")).toBeTruthy();
    expect(screen.getByText("fyers")).toBeTruthy();
    expect(screen.getByText("2600")).toBeTruthy();
  });
});
