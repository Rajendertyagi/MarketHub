import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getSubscriptions: vi.fn(),
  setIndexEnabled: vi.fn(),
  addStock: vi.fn(),
  setStockEnabled: vi.fn(),
  removeStock: vi.fn(),
  putDerivativeRule: vi.fn(),
  deleteDerivativeRule: vi.fn(),
  applySubscriptions: vi.fn(),
}));
vi.mock("@/api/market", () => ({
  getSubscriptions: api.getSubscriptions,
  setIndexEnabled: api.setIndexEnabled,
  addStock: api.addStock,
  setStockEnabled: api.setStockEnabled,
  removeStock: api.removeStock,
  putDerivativeRule: api.putDerivativeRule,
  deleteDerivativeRule: api.deleteDerivativeRule,
  applySubscriptions: api.applySubscriptions,
}));

import { SubscriptionsView } from "@/features/subscriptions/SubscriptionsView";

const CANONICAL = [
  "NIFTY",
  "BANKNIFTY",
  "FINNIFTY",
  "MIDCPNIFTY",
  "NIFTYNXT50",
  "INDIA VIX",
  "SENSEX",
  "BANKEX",
];

function prefs(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    indices: CANONICAL.map((label, i) => ({
      label,
      key: `KEY_${i}`,
      fyers_symbol: `FS_${i}`,
      enabled: true,
      canonical: true,
    })),
    stocks: [
      { key: "NSE:RELIANCE", label: "RELIANCE", enabled: true },
      { key: "NSE:INFY", label: "INFY", enabled: false },
    ],
    derivatives: [
      {
        underlying: "HDFCBANK",
        futures_enabled: true,
        futures_count: 1,
        options_enabled: true,
        options_count: 1,
        strikes_below: 5,
        strikes_above: 5,
        calls_enabled: true,
        puts_enabled: true,
        updated_at: null,
      },
    ],
    ...overrides,
  };
}

function renderWithProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/subscriptions"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("SubscriptionsView", () => {
  it("renders all 8 canonical indices from the backend", async () => {
    api.getSubscriptions.mockResolvedValue(prefs());
    renderWithProviders(<SubscriptionsView />);
    for (const label of CANONICAL) {
      expect(await screen.findByText(label)).toBeTruthy();
    }
  });

  it("preserves exact canonical identity (label, not a redefined list)", async () => {
    api.getSubscriptions.mockResolvedValue(prefs());
    renderWithProviders(<SubscriptionsView />);
    // Every canonical label appears as a row label.
    const rows = await screen.findAllByText(
      /NIFTY|BANKNIFTY|FINNIFTY|MIDCPNIFTY|NIFTYNXT50|INDIA VIX|SENSEX|BANKEX/,
    );
    expect(rows.length).toBeGreaterThanOrEqual(8);
  });

  it("toggles an index subscription", async () => {
    api.getSubscriptions.mockResolvedValue(prefs());
    api.setIndexEnabled.mockResolvedValue({});
    renderWithProviders(<SubscriptionsView />);
    const checkbox = (await screen.findAllByRole("checkbox"))[0];
    if (!checkbox) throw new Error("expected subscription checkbox");
    fireEvent.click(checkbox);
    await waitFor(() => expect(api.setIndexEnabled).toHaveBeenCalledWith("NIFTY", false));
  });

  it("shows error state on load failure", async () => {
    api.getSubscriptions.mockRejectedValue(new Error("subs load failed"));
    renderWithProviders(<SubscriptionsView />);
    expect(await screen.findByText(/subs load failed/)).toBeTruthy();
  });

  it("save/apply reconciles and shows success", async () => {
    api.getSubscriptions.mockResolvedValue(prefs());
    api.applySubscriptions.mockResolvedValue({ status: "ok", resolved_count: 142 });
    renderWithProviders(<SubscriptionsView />);
    await screen.findByText("NIFTY");
    fireEvent.click(screen.getByText(/Save & Apply/));
    await waitFor(() => expect(api.applySubscriptions).toHaveBeenCalled());
    expect(await screen.findByText(/142 contracts resolved/)).toBeTruthy();
  });

  it("shows error when apply fails (server validation)", async () => {
    api.getSubscriptions.mockResolvedValue(prefs());
    api.applySubscriptions.mockRejectedValue(new Error("subscription limit exceeded"));
    renderWithProviders(<SubscriptionsView />);
    await screen.findByText("NIFTY");
    fireEvent.click(screen.getByText(/Save & Apply/));
    expect(await screen.findByText(/subscription limit exceeded/)).toBeTruthy();
  });

  it("edits a derivative rule: current/next expiry, ATM range, CE/PE", async () => {
    api.getSubscriptions.mockResolvedValue(prefs());
    api.putDerivativeRule.mockResolvedValue({});
    renderWithProviders(<SubscriptionsView />);
    // Switch to Derivative Rules tab.
    fireEvent.click(await screen.findByText("Derivative Rules"));
    const editor = screen.getByText("HDFCBANK").closest(".rule-editor") as HTMLElement;
    const selects = within(editor).getAllByRole("combobox") as HTMLElement[];
    const numbers = within(editor).getAllByRole("spinbutton") as HTMLElement[];
    const reqEl = (els: HTMLElement[], i: number, what: string): HTMLElement => {
      const el = els[i];
      if (!el) throw new Error(`expected ${what}`);
      return el;
    };
    // selects order: Futures, FuturesExpiry, Options, OptionsExpiry, Calls, Puts
    // numbers order: ATM below, ATM above
    fireEvent.change(reqEl(selects, 1, "futures expiry select"), { target: { value: "2" } }); // futures expiry -> next
    fireEvent.change(reqEl(numbers, 0, "ATM below input"), { target: { value: "10" } }); // ATM below
    fireEvent.change(reqEl(selects, 4, "calls select"), { target: { value: "off" } }); // Calls (CE) off
    fireEvent.click(within(editor).getByText("Save rule"));
    await waitFor(() => expect(api.putDerivativeRule).toHaveBeenCalled());
    const call = api.putDerivativeRule.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(call.underlying).toBe("HDFCBANK");
    expect(call.futures_count).toBe(2); // current + next
    expect(call.strikes_below).toBe(10); // ATM range
    expect(call.calls_enabled).toBe(false); // CE off
  });

  it("adds and removes a stock subscription", async () => {
    api.getSubscriptions.mockResolvedValue(prefs());
    api.addStock.mockResolvedValue({});
    api.removeStock.mockResolvedValue(true);
    renderWithProviders(<SubscriptionsView />);
    fireEvent.click(await screen.findByText("Stocks"));
    const keyInput = screen.getByPlaceholderText("NSE:RELIANCE");
    fireEvent.change(keyInput, { target: { value: "NSE:TCS" } });
    fireEvent.click(screen.getByText("Add stock"));
    await waitFor(() => expect(api.addStock).toHaveBeenCalledWith("NSE:TCS", "NSE:TCS"));
    // Remove the first stock row.
    const removeBtn = screen.getAllByText("Remove")[0];
    if (!removeBtn) throw new Error("expected Remove button");
    fireEvent.click(removeBtn);
    await waitFor(() => expect(api.removeStock).toHaveBeenCalled());
  });
});
