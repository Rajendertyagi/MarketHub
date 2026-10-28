// Chart indicator menu, driven only by the shared indicator catalog.
//
// The action takes over Vela's `indicators` topbar slot (and with it the mobile
// stop and the `/` chord), which also stops the shell from constructing its own
// native picker — this menu is the single entry point. Adding goes through
// `ctx.addNativeIndicator`, so undo/redo and the persisted chart document stay
// authoritative.

import {
  type IndicatorHandle,
  type NativeIndicatorInfo,
  registerWidgetAction,
  registerWidgetAttachment,
  type WidgetContext,
} from "@luxalgo/vela";
import { createElement, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  buildIndicatorCatalog,
  INDICATOR_CATEGORY_ORDER,
  type IndicatorCatalogEntry,
} from "./indicatorCatalog";

export const INDICATOR_SLOT_ID = "indicators";
const ATTACHMENT_ID = "markethub-indicators";

interface OverlayApi {
  show(): void;
  hide(): void;
}

class IndicatorMenuController {
  private api: OverlayApi | null = null;
  private open = false;

  constructor(readonly ctx: WidgetContext) {}

  connect(api: OverlayApi | null) {
    this.api = api;
  }

  show() {
    this.open = true;
    this.api?.show();
  }

  close() {
    this.open = false;
    this.api?.hide();
  }

  get isOpen(): boolean {
    return this.open;
  }
}

const controllers = new WeakMap<HTMLElement, IndicatorMenuController>();

function activeChart(ctx: WidgetContext) {
  try {
    return ctx.chart;
  } catch {
    return null;
  }
}

function matchesName(name: string, aliases: readonly string[], needle: string): boolean {
  if (!needle) return true;
  return [name, ...aliases].some((value) => value.toLowerCase().includes(needle));
}

function IndicatorMenu({ controller }: { controller: IndicatorMenuController }) {
  const ctx = controller.ctx;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [catalog, setCatalog] = useState<IndicatorCatalogEntry[]>([]);
  const [infos, setInfos] = useState<NativeIndicatorInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [onChart, setOnChart] = useState<IndicatorHandle[]>([]);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);
  const requestRef = useRef(0);

  useEffect(() => {
    controller.connect({
      show() {
        setQuery("");
        setOpen(true);
      },
      hide() {
        setOpen(false);
      },
    });
    return () => controller.connect(null);
  }, [controller]);

  const syncHandles = useCallback(() => {
    const chart = activeChart(ctx);
    setOnChart(chart?.indicators().filter((handle) => handle.nativeType !== undefined) ?? []);
  }, [ctx]);

  const loadInfos = useCallback(() => {
    const chart = activeChart(ctx);
    const request = ++requestRef.current;
    setCatalog(buildIndicatorCatalog(ctx.i18n));
    setInfos(null);
    setError(null);
    syncHandles();
    if (!chart) {
      setInfos([]);
      setError("The chart is not ready yet.");
      return;
    }
    chart.availableNativeIndicators().then(
      (list) => {
        if (requestRef.current === request) setInfos(list);
      },
      () => {
        if (requestRef.current !== request) return;
        setInfos([]);
        setError("Could not load the indicator catalog.");
      },
    );
  }, [ctx, syncHandles]);

  useEffect(() => {
    if (!open) return;
    loadInfos();
    return () => {
      requestRef.current += 1;
    };
  }, [open, loadInfos]);

  useEffect(() => {
    if (!open) return;
    searchRef.current?.focus();
  }, [open]);

  // The chart materializes its own defaults (volume) and the shell applies adds
  // asynchronously; both change the handle list without a menu-driven sync.
  useEffect(() => {
    if (!open) return;
    const chart = activeChart(ctx);
    if (!chart) return;
    const offAdded = chart.on("indicator:added", syncHandles);
    const offRemoved = chart.on("indicator:removed", syncHandles);
    return () => {
      offAdded();
      offRemoved();
    };
  }, [open, ctx, syncHandles]);

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

  const chart = open ? activeChart(ctx) : null;
  const presentTypes = new Set(chart?.presentNativeIndicators() ?? []);
  const needle = query.trim().toLowerCase();

  const groups = useMemo(() => {
    if (!infos) return [];
    const supported = new Set(infos.filter((info) => info.supported).map((info) => info.type));
    const byCategory = new Map<string, IndicatorCatalogEntry[]>();
    for (const entry of catalog) {
      if (!supported.has(entry.type)) continue;
      if (!matchesName(entry.title, entry.aliases, needle)) continue;
      const bucket = byCategory.get(entry.category) ?? [];
      bucket.push(entry);
      byCategory.set(entry.category, bucket);
    }
    return INDICATOR_CATEGORY_ORDER.filter((category) => byCategory.has(category)).map(
      (category) => [category, byCategory.get(category) ?? []] as const,
    );
  }, [catalog, infos, needle]);

  const add = (entry: IndicatorCatalogEntry) => {
    try {
      ctx.addNativeIndicator(entry.type);
      syncHandles();
    } catch {
      setError("The chart is not ready yet.");
    }
  };

  const remove = (id: string) => {
    activeChart(ctx)
      ?.indicators()
      .find((handle) => handle.id === id)
      ?.remove();
    syncHandles();
  };

  if (!open) return null;

  const loading = infos === null && error === null;
  const empty = !loading && !error && groups.length === 0 && onChart.length === 0;

  return (
    <div className="chart-overlay-root">
      <div className="chart-overlay-scrim" />
      <div
        ref={panelRef}
        className="chart-overlay-panel chart-indicator-panel"
        role="dialog"
        aria-label="Indicators"
      >
        <div className="chart-overlay-head">
          <span className="chart-overlay-title">Indicators</span>
          <button
            type="button"
            className="chart-overlay-close"
            aria-label="Close indicators"
            onClick={() => controller.close()}
          >
            ×
          </button>
        </div>

        <input
          ref={searchRef}
          type="search"
          className="chart-indicator-search"
          aria-label="Search indicators"
          placeholder="Search indicators…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />

        <div className="chart-indicator-body">
          {loading && <div className="chart-indicator-note muted">Loading indicators…</div>}
          {error && <div className="chart-indicator-note err">{error}</div>}

          {onChart.length > 0 && (
            <section className="chart-indicator-group" aria-label="On chart">
              <h4 className="chart-indicator-group-title">On chart</h4>
              {onChart.map((handle) => (
                <div className="chart-indicator-row" key={handle.id}>
                  <span className="chart-indicator-name">{handle.title}</span>
                  <span className="chart-indicator-badge">chart</span>
                  <button
                    type="button"
                    className="chart-indicator-remove"
                    aria-label={`Remove ${handle.title}`}
                    onClick={() => remove(handle.id)}
                  >
                    ×
                  </button>
                </div>
              ))}
            </section>
          )}

          {groups.map(([category, entries]) => (
            <section className="chart-indicator-group" aria-label={category} key={category}>
              <h4 className="chart-indicator-group-title">{category}</h4>
              {entries.map((entry) => {
                const present = presentTypes.has(entry.type);
                const disabled = present && !entry.multiInstance;
                return (
                  <div className="chart-indicator-row" key={entry.type}>
                    <button
                      type="button"
                      className="chart-indicator-add"
                      disabled={disabled}
                      title={entry.type}
                      onClick={() => add(entry)}
                    >
                      <span className="chart-indicator-name">{entry.title}</span>
                      {entry.shortTitle && entry.shortTitle !== entry.title && (
                        <span className="chart-indicator-alias">{entry.shortTitle}</span>
                      )}
                      {present && (
                        <span className="chart-indicator-badge on">
                          {entry.multiInstance ? "added" : "on chart"}
                        </span>
                      )}
                      {entry.beta && <span className="chart-indicator-badge">beta</span>}
                    </button>
                  </div>
                );
              })}
            </section>
          ))}

          {empty && (
            <div className="chart-indicator-note muted">
              {needle ? "No matching indicators." : "No indicators available."}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

registerWidgetAction({
  id: INDICATOR_SLOT_ID,
  target: "topbar",
  label: "Indicators",
  icon: "indicators",
  align: "left",
  run: (ctx: WidgetContext) => controllers.get(ctx.host)?.show(),
});

registerWidgetAttachment({
  id: ATTACHMENT_ID,
  mount(ctx: WidgetContext) {
    const container = document.createElement("div");
    container.className = "chart-indicator-root";
    ctx.host.appendChild(container);

    const controller = new IndicatorMenuController(ctx);
    controllers.set(ctx.host, controller);
    const root = createRoot(container);
    root.render(createElement(IndicatorMenu, { controller }));

    return () => {
      controllers.delete(ctx.host);
      root.unmount();
      container.remove();
    };
  },
});
