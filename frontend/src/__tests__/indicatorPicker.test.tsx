import { registerWidgetAction, registerWidgetAttachment, type WidgetContext } from "@luxalgo/vela";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const vela = vi.hoisted(() => {
  const descriptor = (over: Record<string, unknown>) => ({
    multiInstance: false,
    beta: false,
    inputsSchema: () => [],
    defaultInputs: () => ({}),
    ...over,
  });
  return {
    descriptors: [
      descriptor({
        type: "sma",
        title: "Simple Moving Average",
        shortTitle: "SMA",
        multiInstance: true,
      }),
      descriptor({ type: "rsi", title: "Relative Strength Index", shortTitle: "RSI" }),
      descriptor({ type: "future-thing", title: "Future Thing" }),
    ],
  };
});

vi.mock("@luxalgo/vela", () => ({
  nativeIndicatorDescriptors: vi.fn(() => vela.descriptors),
  registerWidgetAction: vi.fn(),
  registerWidgetAttachment: vi.fn(),
}));

import { INDICATOR_SLOT_ID } from "@/features/chart/indicatorPicker";

const registeredAction = vi
  .mocked(registerWidgetAction)
  .mock.calls.find(([options]) => options.id === INDICATOR_SLOT_ID)?.[0];
const registeredAttachment = vi
  .mocked(registerWidgetAttachment)
  .mock.calls.find(([options]) => options.id === "markethub-indicators")?.[0];

function indicatorAction() {
  if (!registeredAction) throw new Error("indicator action was not registered");
  return registeredAction;
}

function indicatorAttachment() {
  if (!registeredAttachment) throw new Error("indicator attachment was not registered");
  return registeredAttachment;
}

interface FakeHandle {
  id: string;
  title: string;
  nativeType: string;
  remove: ReturnType<typeof vi.fn>;
}

let dispose: (() => void) | null = null;
let container: HTMLDivElement;
let host: HTMLDivElement;
let ctx: WidgetContext;
let handles: Map<string, FakeHandle>;
let present: string[];
let addNativeIndicator: ReturnType<typeof vi.fn>;
let emit: (event: string) => void;

async function mount({ failCatalog = false } = {}) {
  handles = new Map();
  present = [];
  const listeners = new Map<string, Set<() => void>>();
  emit = (event) => {
    for (const listener of listeners.get(event) ?? []) listener();
  };
  addNativeIndicator = vi.fn((type: string) => {
    const id = `${type}-${handles.size}`;
    const handle: FakeHandle = {
      id,
      title: type === "sma" ? "Simple Moving Average" : "Relative Strength Index",
      nativeType: type,
      remove: vi.fn(() => {
        handles.delete(id);
        present = present.filter((value) => value !== type);
      }),
    };
    handles.set(id, handle);
    present = [...present, type];
    return handle;
  });
  const availableNativeIndicators = failCatalog
    ? vi.fn(async (): Promise<never> => {
        throw new Error("catalog unavailable");
      })
    : vi.fn(async () => [
        { type: "sma", title: "Simple Moving Average", supported: true },
        { type: "rsi", title: "Relative Strength Index", supported: true },
        { type: "future-thing", title: "Future Thing", supported: false },
      ]);

  const chart = {
    availableNativeIndicators,
    indicators: () => [...handles.values()],
    presentNativeIndicators: () => present,
    on: (event: string, listener: () => void) => {
      const bucket = listeners.get(event) ?? new Set();
      bucket.add(listener);
      listeners.set(event, bucket);
      return () => bucket.delete(listener);
    },
  };

  container = document.createElement("div");
  host = document.createElement("div");
  host.className = "vela-workspace";
  container.appendChild(host);
  document.body.appendChild(container);

  ctx = {
    host,
    i18n: {},
    chart,
    addNativeIndicator,
  } as unknown as WidgetContext;
  await act(async () => {
    dispose = indicatorAttachment().mount(ctx);
  });
}

async function openMenu() {
  await act(async () => {
    indicatorAction().run(ctx);
  });
  return screen.findByRole("dialog", { name: "Indicators" });
}

afterEach(() => {
  act(() => dispose?.());
  dispose = null;
  container?.remove();
  document.body.innerHTML = "";
  vi.clearAllMocks();
});

describe("chart indicator menu", () => {
  it("registers the native indicators slot and one attachment", async () => {
    await mount();
    expect(indicatorAction()).toMatchObject({
      id: INDICATOR_SLOT_ID,
      target: "topbar",
      align: "left",
    });
    expect(indicatorAttachment().id).toBe("markethub-indicators");
  });

  it("lists only indicators the chart supports, without Vela's native picker", async () => {
    await mount();
    await openMenu();

    expect(
      await screen.findByRole("button", { name: /Simple Moving Average/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Relative Strength Index/ })).toBeInTheDocument();
    expect(screen.queryByText("Future Thing")).not.toBeInTheDocument();
    expect(document.querySelector(".vela-ip-search")).toBeNull();
    expect(document.querySelector(".vela-dialog-backdrop")).toBeNull();
  });

  it("adds through the shell so undo, persistence and the chart stay authoritative", async () => {
    await mount();
    await openMenu();

    const sma = await screen.findByRole("button", { name: /Simple Moving Average/ });
    fireEvent.click(sma);
    expect(addNativeIndicator).toHaveBeenCalledWith("sma");

    const remove = await screen.findByRole("button", { name: "Remove Simple Moving Average" });
    expect(screen.getByText("On chart")).toBeInTheDocument();
    expect(sma).toHaveTextContent("added");

    fireEvent.click(sma);
    expect(addNativeIndicator).toHaveBeenCalledTimes(2);
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "Remove Simple Moving Average" })).toHaveLength(
        2,
      ),
    );

    fireEvent.click(remove);
    expect(handles.size).toBe(1);
  });

  it("blocks a second instance of a single-instance indicator", async () => {
    await mount();
    await openMenu();

    const rsi = await screen.findByRole("button", { name: /Relative Strength Index/ });
    fireEvent.click(rsi);

    await waitFor(() => expect(rsi).toBeDisabled());
    expect(rsi).toHaveTextContent("on chart");
    expect(
      screen.getByRole("button", { name: "Remove Relative Strength Index" }),
    ).toBeInTheDocument();

    const handle = [...handles.values()][0];
    fireEvent.click(screen.getByRole("button", { name: "Remove Relative Strength Index" }));
    expect(handle?.remove).toHaveBeenCalled();
  });

  it("keeps on-chart rows in sync when the chart materializes its own indicators", async () => {
    await mount();
    await openMenu();
    expect(screen.queryByText("On chart")).not.toBeInTheDocument();

    handles.set("volume-0", {
      id: "volume-0",
      title: "Volume",
      nativeType: "volume",
      remove: vi.fn(),
    });
    await act(async () => emit("indicator:added"));

    expect(screen.getByText("On chart")).toBeInTheDocument();
    expect(screen.getByText("Volume")).toBeInTheDocument();
  });

  it("searches titles and aliases and reports an empty result", async () => {
    await mount();
    await openMenu();
    await screen.findByRole("button", { name: /Simple Moving Average/ });
    const search = screen.getByLabelText("Search indicators");

    fireEvent.change(search, { target: { value: "relative strength" } });
    expect(screen.queryByRole("button", { name: /Simple Moving Average/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Relative Strength Index/ })).toBeInTheDocument();

    fireEvent.change(search, { target: { value: "zzz" } });
    expect(screen.getByText("No matching indicators.")).toBeInTheDocument();
  });

  it("surfaces a catalog load failure", async () => {
    await mount({ failCatalog: true });

    await openMenu();
    expect(await screen.findByText("Could not load the indicator catalog.")).toBeInTheDocument();
  });

  it("closes on Escape and on an outside pointer press", async () => {
    await mount();
    await openMenu();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    await openMenu();
    fireEvent.mouseDown(document.body);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
