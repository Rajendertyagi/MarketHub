// Chart symbol selection from inside the Vela layout.
//
// Vela's own symbol picker reads `feed.symbols()`, which is empty for a
// server-side catalogue (`capabilities.enumerate: false`), so the chart owns
// symbol selection: a contributed topbar action plus one attachment that mounts
// the shared InstrumentPicker into `ctx.host`. The native typing chord is
// bridged to the same picker — no call to `ctx.openSymbolSearch()` and no
// native symbol dialog in any path.

import { registerWidgetAction, registerWidgetAttachment, type WidgetContext } from "@luxalgo/vela";
import { createElement, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { toInstrument } from "@/api/market";
import { InstrumentPicker } from "@/components/InstrumentPicker";
import type { Instrument, MarketInstrument } from "@/types";

export const MARKETHUB_SYMBOL_ACTION_ID = "markethub-symbol";
const ATTACHMENT_ID = "markethub-symbol-picker";

export interface ChartSymbolHandlers {
  onSelect(instrument: Instrument): void;
}

interface OverlayApi {
  show(seed?: string): void;
  hide(): void;
}

class ChartSymbolController {
  private api: OverlayApi | null = null;
  private handlers: ChartSymbolHandlers | null = null;
  private open = false;

  constructor(readonly host: HTMLElement) {}

  connect(api: OverlayApi | null) {
    this.api = api;
  }

  bind(handlers: ChartSymbolHandlers | null) {
    this.handlers = handlers;
  }

  show(seed = "") {
    this.open = true;
    this.api?.show(seed);
  }

  close() {
    this.open = false;
    this.api?.hide();
  }

  get isOpen(): boolean {
    return this.open;
  }

  pick(row: MarketInstrument) {
    this.open = false;
    this.api?.hide();
    this.handlers?.onSelect(toInstrument(row));
  }
}

const controllers = new WeakMap<HTMLElement, ChartSymbolController>();

/** Register the URL/chart update for the workspace created inside `container`. */
export function bindChartSymbolHandlers(
  container: HTMLElement,
  handlers: ChartSymbolHandlers,
): () => void {
  const controller = findController(container);
  if (!controller) return () => {};
  controller.bind(handlers);
  return () => controller.bind(null);
}

function findController(container: HTMLElement): ChartSymbolController | undefined {
  const host = container.querySelector<HTMLElement>(".vela-workspace");
  return host ? controllers.get(host) : undefined;
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return true;
  if (target.isContentEditable) return true;
  return target.getAttribute("role") === "textbox";
}

/** A Vela dialog is open — the same gate Vela's own typing router uses. */
function hasOpenDialog(host: HTMLElement): boolean {
  for (const el of host.querySelectorAll<HTMLElement>(".vela-dialog-backdrop")) {
    if (el.style.display !== "none") return true;
  }
  return false;
}

/**
 * The seeded symbol search a bare keystroke should open, or null when the key
 * belongs to someone else. Mirrors Vela's typing router: letters and `0` seed
 * the symbol picker, digits 1-9 stay with the timeframe entry.
 */
export function symbolSeedFor(
  event: KeyboardEvent,
  state: { pickerOpen: boolean; dialogOpen: boolean },
): string | null {
  if (event.defaultPrevented) return null;
  if (event.ctrlKey || event.metaKey || event.altKey) return null;
  if (event.isComposing || event.key === "Process") return null;
  if (isEditableTarget(event.target)) return null;
  if (state.pickerOpen || state.dialogOpen) return null;
  if ([...event.key].length !== 1) return null;
  if (!/^[\p{L}0]$/u.test(event.key)) return null;
  return event.key.toUpperCase();
}

function SymbolPickerOverlay({ controller }: { controller: ChartSymbolController }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const panelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    controller.connect({
      show(seed = "") {
        setQuery(seed);
        setOpen(true);
      },
      hide() {
        setOpen(false);
      },
    });
    return () => controller.connect(null);
  }, [controller]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!panelRef.current?.contains(event.target as Node)) controller.close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") controller.close();
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [open, controller]);

  if (!open) return null;

  return (
    <div className="chart-overlay-root">
      <div className="chart-overlay-scrim" />
      <div
        ref={panelRef}
        className="chart-overlay-panel chart-symbol-panel"
        role="dialog"
        aria-label="Change symbol"
      >
        <div className="chart-overlay-head">
          <span className="chart-overlay-title">Symbol</span>
          <button
            type="button"
            className="chart-overlay-close"
            aria-label="Close symbol search"
            onClick={() => controller.close()}
          >
            ×
          </button>
        </div>
        <InstrumentPicker
          value={query}
          onChange={setQuery}
          onSelect={(row) => controller.pick(row)}
          placeholder="Search symbol or option (NIFTY, RELIANCE CE)…"
          ariaLabel="Search instrument"
          limit={15}
          fno={false}
          autoFocus
        />
      </div>
    </div>
  );
}

registerWidgetAction({
  id: MARKETHUB_SYMBOL_ACTION_ID,
  target: "topbar",
  label: "Symbol",
  icon: "search",
  align: "left",
  run: (ctx: WidgetContext) => controllers.get(ctx.host)?.show(),
});

registerWidgetAttachment({
  id: ATTACHMENT_ID,
  mount(ctx: WidgetContext) {
    // The mobile bar's own symbol stop opens Vela's (empty) native picker.
    // The contributed action is the single symbol entry point on both layouts.
    const mobileSymbol = ctx.host.querySelector<HTMLElement>(".vela-mb-symbol");
    if (mobileSymbol) mobileSymbol.hidden = true;

    const container = document.createElement("div");
    container.className = "chart-symbol-root";
    ctx.host.appendChild(container);

    const controller = new ChartSymbolController(ctx.host);
    controllers.set(ctx.host, controller);
    const root = createRoot(container);
    root.render(createElement(SymbolPickerOverlay, { controller }));

    const onKeyDown = (event: KeyboardEvent) => {
      const seed = symbolSeedFor(event, {
        pickerOpen: controller.isOpen,
        dialogOpen: hasOpenDialog(ctx.host),
      });
      if (seed === null) return;
      event.preventDefault();
      // Vela's typing router listens on this same node; a plain
      // stopPropagation would still let it run.
      event.stopImmediatePropagation();
      controller.show(seed);
    };
    ctx.host.addEventListener("keydown", onKeyDown, true);

    return () => {
      ctx.host.removeEventListener("keydown", onKeyDown, true);
      controllers.delete(ctx.host);
      root.unmount();
      container.remove();
    };
  },
});
