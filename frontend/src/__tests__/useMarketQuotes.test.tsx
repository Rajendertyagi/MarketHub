import { act, cleanup, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QUOTE_FLUSH_MS } from "@/features/dashboard/constants";
import type { MarketQuote } from "@/features/dashboard/types";
import type { StreamEvent } from "@/streams/SSEManager";

type QuoteHandler = (event: StreamEvent<MarketQuote>) => void;

const harness = vi.hoisted(() => ({
  onQuote: null as QuoteHandler | null,
  onReset: null as (() => void) | null,
  onOpen: null as (() => void) | null,
  onError: null as (() => void) | null,
}));

// The hook's SSE binding is replaced by a captured handler so tests can emit
// ticks directly; the real stream wiring is covered by SSEManager tests.
vi.mock("@/streams/useStream", () => ({
  useStream: (
    _key: string,
    _url: string,
    handler: QuoteHandler,
    options: {
      onReset?: () => void;
      onOpen?: () => void;
      onError?: () => void;
    } = {},
  ) => {
    harness.onQuote = handler;
    harness.onReset = options.onReset ?? null;
    harness.onOpen = options.onOpen ?? null;
    harness.onError = options.onError ?? null;
  },
}));

const api = vi.hoisted(() => ({ getMarketQuotes: vi.fn() }));
vi.mock("@/features/dashboard/api", () => ({
  getMarketQuotes: api.getMarketQuotes,
  getQuoteDepth: vi.fn(),
}));

import { useMarketQuotes } from "@/features/dashboard/useMarketQuotes";

const strict = ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>;

function setHidden(value: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => value });
}

const quote = (ltp: number): MarketQuote => ({
  exchange: "NSE",
  instrument_token: "NIFTY50",
  ltp,
  canonical_id: "NSE:NIFTY50-INDEX",
});

beforeEach(() => {
  api.getMarketQuotes.mockReset();
  api.getMarketQuotes.mockResolvedValue({ quotes: [] });
  harness.onQuote = null;
  harness.onReset = null;
  harness.onOpen = null;
  harness.onError = null;
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  Reflect.deleteProperty(document, "hidden");
});

describe("useMarketQuotes", () => {
  // Runs first: the snapshot promise is module-scoped, so this is the only
  // test that can observe its request count.
  it("shares one snapshot request across StrictMode remounts", async () => {
    const first = renderHook(() => useMarketQuotes(), { wrapper: strict });
    await act(async () => {});
    expect(api.getMarketQuotes).toHaveBeenCalledTimes(1);

    first.unmount();
    renderHook(() => useMarketQuotes(), { wrapper: strict });
    await act(async () => {});
    expect(api.getMarketQuotes).toHaveBeenCalledTimes(1);
  });

  it("does not re-render while the quote map is unchanged", async () => {
    vi.useFakeTimers();
    let renders = 0;
    const { result } = renderHook(() => {
      renders += 1;
      return useMarketQuotes();
    });
    await act(async () => {});
    expect(result.current.quotes).toEqual([]);

    const settled = renders;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(renders).toBe(settled);
  });

  it("buffers ticks, pauses while hidden, and flushes on visibility", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useMarketQuotes());
    await act(async () => {});

    act(() => {
      harness.onQuote?.({ type: "quote", data: quote(1) });
    });
    expect(result.current.quotes).toEqual([]);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(QUOTE_FLUSH_MS);
    });
    expect(result.current.quotes).toHaveLength(1);
    expect(result.current.byCanonical.get("NSE:NIFTY50-INDEX")?.ltp).toBe(1);

    setHidden(true);
    act(() => {
      harness.onQuote?.({ type: "quote", data: quote(2) });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(result.current.quotes[0]?.ltp).toBe(1);

    setHidden(false);
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(result.current.quotes[0]?.ltp).toBe(2);
  });

  it("drops state on a stream reset and does not resurrect it", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useMarketQuotes());
    await act(async () => {});

    act(() => {
      harness.onQuote?.({ type: "quote", data: quote(1) });
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(QUOTE_FLUSH_MS);
    });
    expect(result.current.quotes).toHaveLength(1);

    act(() => {
      harness.onReset?.();
    });
    expect(result.current.quotes).toEqual([]);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(result.current.quotes).toEqual([]);
  });

  it("tracks stream connection state", async () => {
    const { result } = renderHook(() => useMarketQuotes());
    await act(async () => {});

    act(() => {
      harness.onOpen?.();
    });
    expect(result.current.connected).toBe(true);

    act(() => {
      harness.onError?.();
    });
    expect(result.current.connected).toBe(false);
  });
});
