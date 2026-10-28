import { registerWidgetAction, registerWidgetAttachment, type WidgetContext } from "@luxalgo/vela";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@luxalgo/vela", () => ({
  registerWidgetAction: vi.fn(),
  registerWidgetAttachment: vi.fn(),
  nativeIndicatorDescriptors: vi.fn(() => []),
}));

vi.mock("@/api/market", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/market")>();
  return { ...actual, searchMarketInstruments: vi.fn() };
});

import { searchMarketInstruments } from "@/api/market";
import {
  bindChartSymbolHandlers,
  MARKETHUB_SYMBOL_ACTION_ID,
  symbolSeedFor,
} from "@/features/chart/chartSymbolPicker";
import type { Instrument, MarketInstrument } from "@/types";

const RELIANCE: MarketInstrument = {
  instrument_key: "NSE:NSE_EQ|INE002A01018",
  provider: "upstox",
  provider_token: "NSE_EQ|INE002A01018",
  symbol: "RELIANCE",
  exchange: "NSE",
  type: "EQUITY",
};

const mockSearch = vi.mocked(searchMarketInstruments);

const registeredAction = vi
  .mocked(registerWidgetAction)
  .mock.calls.find(([options]) => options.id === MARKETHUB_SYMBOL_ACTION_ID)?.[0];
const registeredAttachment = vi
  .mocked(registerWidgetAttachment)
  .mock.calls.find(([options]) => options.id === "markethub-symbol-picker")?.[0];

function chartAction() {
  if (!registeredAction) throw new Error("symbol action was not registered");
  return registeredAction;
}

function symbolAttachment() {
  if (!registeredAttachment) throw new Error("symbol attachment was not registered");
  return registeredAttachment;
}

let dispose: (() => void) | null = null;
let container: HTMLDivElement;
let host: HTMLDivElement;
let ctx: WidgetContext;

async function mount(handlers?: { onSelect: (instrument: Instrument) => void }) {
  container = document.createElement("div");
  host = document.createElement("div");
  host.className = "vela-workspace";
  host.innerHTML = '<div class="vela-mb-symbol"></div>';
  container.appendChild(host);
  document.body.appendChild(container);

  ctx = { host, i18n: {} } as unknown as WidgetContext;
  await act(async () => {
    dispose = symbolAttachment().mount(ctx);
  });
  if (handlers) bindChartSymbolHandlers(container, handlers);
  return ctx;
}

async function openPicker() {
  await act(async () => {
    chartAction().run(ctx);
  });
  return screen.findByRole("dialog", { name: "Change symbol" });
}

beforeEach(() => {
  mockSearch.mockReset();
});

afterEach(() => {
  act(() => dispose?.());
  dispose = null;
  container?.remove();
  document.body.innerHTML = "";
  vi.clearAllMocks();
});

describe("symbolSeedFor", () => {
  const idle = { pickerOpen: false, dialogOpen: false };
  const key = (value: string, init: KeyboardEventInit = {}) =>
    new KeyboardEvent("keydown", { key: value, cancelable: true, ...init });

  it("seeds a letter or zero, uppercased", () => {
    expect(symbolSeedFor(key("r"), idle)).toBe("R");
    expect(symbolSeedFor(key("0"), idle)).toBe("0");
  });

  it("leaves digits 1-9 to the timeframe entry", () => {
    expect(symbolSeedFor(key("2"), idle)).toBeNull();
  });

  it("ignores modified, composing, and multi-character keys", () => {
    expect(symbolSeedFor(key("r", { ctrlKey: true }), idle)).toBeNull();
    expect(symbolSeedFor(key("r", { metaKey: true }), idle)).toBeNull();
    expect(symbolSeedFor(key("r", { altKey: true }), idle)).toBeNull();
    expect(symbolSeedFor(key("Process"), idle)).toBeNull();
    expect(symbolSeedFor(key("Enter"), idle)).toBeNull();
    expect(
      symbolSeedFor(new KeyboardEvent("keydown", { key: "r", isComposing: true }), idle),
    ).toBeNull();
  });

  it("ignores keys aimed at an editable target", () => {
    const input = document.createElement("input");
    document.body.appendChild(input);
    const event = key("r");
    Object.defineProperty(event, "target", { value: input });
    expect(symbolSeedFor(event, idle)).toBeNull();
  });

  it("stays out of the way of an open picker or a Vela dialog", () => {
    expect(symbolSeedFor(key("r"), { pickerOpen: true, dialogOpen: false })).toBeNull();
    expect(symbolSeedFor(key("r"), { pickerOpen: false, dialogOpen: true })).toBeNull();
    const handled = key("r");
    handled.preventDefault();
    expect(symbolSeedFor(handled, idle)).toBeNull();
  });
});

describe("chart symbol wiring", () => {
  it("registers a left-aligned topbar action and one attachment", () => {
    expect(chartAction()).toMatchObject({
      id: MARKETHUB_SYMBOL_ACTION_ID,
      target: "topbar",
      align: "left",
    });
    expect(symbolAttachment().id).toBe("markethub-symbol-picker");
  });

  it("hides the mobile native symbol stop on mount", async () => {
    await mount();
    const mobile = host.querySelector<HTMLElement>(".vela-mb-symbol");
    expect(mobile?.hidden).toBe(true);
  });

  it("opens the shared picker from the topbar action and reports the canonical row", async () => {
    const onSelect = vi.fn();
    await mount({ onSelect });
    mockSearch.mockResolvedValue([RELIANCE]);

    await openPicker();
    expect(document.querySelector(".vela-dialog-backdrop")).toBeNull();

    fireEvent.change(screen.getByLabelText("Search instrument"), { target: { value: "REL" } });
    fireEvent.click(await screen.findByRole("option", { name: /RELIANCE/ }));

    expect(onSelect).toHaveBeenCalledWith({
      instrument_token: "NSE_EQ|INE002A01018",
      exchange: "NSE",
      tradingsymbol: "RELIANCE",
      instrument_type: "EQUITY",
      provider: "upstox",
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("bridges the typing chord into the picker without reaching Vela's router", async () => {
    await mount();
    mockSearch.mockResolvedValue([RELIANCE]);
    const nativeRouter = vi.fn();
    host.addEventListener("keydown", nativeRouter);
    const inner = host.querySelector<HTMLElement>(".vela-mb-symbol") ?? host;

    await act(async () => {
      inner.dispatchEvent(
        new KeyboardEvent("keydown", { key: "r", bubbles: true, cancelable: true }),
      );
    });

    await screen.findByRole("dialog", { name: "Change symbol" });
    expect(screen.getByLabelText("Search instrument")).toHaveValue("R");
    expect(nativeRouter).not.toHaveBeenCalled();
  });

  it("closes on Escape and on an outside pointer press", async () => {
    await mount({ onSelect: vi.fn() });

    await openPicker();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await openPicker();
    fireEvent.mouseDown(document.body);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
