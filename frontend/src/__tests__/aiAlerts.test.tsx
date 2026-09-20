import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getConditionAlerts: vi.fn(),
  getTriggeredEvents: vi.fn(),
  getConsumers: vi.fn(),
}));
vi.mock("@/features/ai-alerts/api", () => ({
  getConditionAlerts: api.getConditionAlerts,
  getTriggeredEvents: api.getTriggeredEvents,
  getConsumers: api.getConsumers,
}));

import { AiAlertsView } from "@/features/ai-alerts";

const ALERT = {
  alert_id: "a1b2c3d4-e5f6-7890",
  consumer_id: "consumer-1",
  name: null,
  enabled: true,
  trigger_mode: "repeat",
  condition_summary: "LTP > 2500",
  condition_json: null,
  instrument: "NSE_EQ|RELIANCE",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: null,
  last_triggered_at: "2026-01-02T00:00:00Z",
  trigger_count: 3,
  metadata: null,
  current_state: "armed",
  crossing_side: "above",
  state_updated_at: null,
};
const EVENT = {
  event_id: 123,
  sequence: 9,
  alert_id: "a1b2c3d4-e5f6-7890",
  source: "alert.engine",
  trigger_time: "2026-01-02T00:00:00Z",
  created_at: "2026-01-02T00:00:00Z",
  consumer_id: "consumer-1",
  condition_summary: "LTP > 2500",
  instrument: "NSE_EQ|RELIANCE",
  delivery_state: "acknowledged",
  delivered_at: "2026-01-02T00:00:01Z",
  acknowledged_at: "2026-01-02T00:00:02Z",
};
const CONSUMER = {
  consumer_id: "consumer-1",
  created_at: "2026-01-01T00:00:00Z",
  pending_count: 0,
  last_triggered: {
    event_id: 123,
    trigger_time: "2026-01-02T00:00:00Z",
    alert_id: "a1b2c3d4-e5f6-7890",
  },
  last_checkpoint: { last_sequence: 9, updated_at: "2026-01-02T00:00:00Z" },
  unacknowledged_count: 0,
};

function renderWithProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/ai-alerts"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function clickTab(text: string) {
  (screen.getByText(text) as HTMLElement).click();
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AiAlertsView", () => {
  it("renders the three panels from backend data (exact identity)", async () => {
    api.getConditionAlerts.mockResolvedValue({ alerts: [ALERT], count: 1 });
    api.getTriggeredEvents.mockResolvedValue({ events: [EVENT], count: 1 });
    api.getConsumers.mockResolvedValue({ consumers: [CONSUMER], count: 1 });

    renderWithProviders(<AiAlertsView />);

    expect(await screen.findByText("AI Alerts")).toBeTruthy();
    // Consumers tab is default.
    expect(await screen.findByText("consumer-1")).toBeTruthy();
    expect(await screen.findByText((content) => content.includes("Checkpoint: #9"))).toBeTruthy();

    // Switch to Condition Alerts.
    clickTab("Condition Alerts");
    expect(await screen.findByText("LTP > 2500")).toBeTruthy();
    expect(await screen.findByText("a1b2c3d4-e5f")).toBeTruthy();

    // Switch to Triggered Events.
    clickTab("Triggered Events");
    expect(await screen.findByText("Acknowledged")).toBeTruthy();
  });

  it("shows error state on load failure (alerts tab)", async () => {
    api.getConditionAlerts.mockRejectedValue(new Error("ai load failed"));
    api.getTriggeredEvents.mockRejectedValue(new Error("ai load failed"));
    api.getConsumers.mockRejectedValue(new Error("ai load failed"));

    renderWithProviders(<AiAlertsView />);
    clickTab("Condition Alerts");
    expect(await screen.findByText(/ai load failed/)).toBeTruthy();
  });
});
