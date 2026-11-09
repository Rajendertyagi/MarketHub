// Phase 2: browse-first behavior of the shared InstrumentPicker.
// Covers the brief's test requirements 2–12 at the component level; the
// page-level composition lives in catalog.test.tsx.

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/market", () => ({
  searchMarketInstruments: vi.fn(),
  browseInstruments: vi.fn(),
  getOptionChainView: vi.fn(),
  resolveMarketInstrument: vi.fn(),
}));

import {
  browseInstruments,
  getOptionChainView,
  resolveMarketInstrument,
  searchMarketInstruments,
} from "@/api/market";
import { InstrumentPicker } from "@/components/InstrumentPicker";
import type { BrowseResponse, MarketInstrument, OptionChainView } from "@/types";

const mockBrowse = browseInstruments as ReturnType<typeof vi.fn>;
const mockSearch = searchMarketInstruments as ReturnType<typeof vi.fn>;
const mockChain = getOptionChainView as ReturnType<typeof vi.fn>;
const mockResolve = resolveMarketInstrument as ReturnType<typeof vi.fn>;

function equity(symbol: string, exchange = "NSE"): MarketInstrument {
  return {
    instrument_key: `${exchange}:${exchange}:${symbol}-EQ`,
    provider: "fyers",
    provider_token: `${exchange}:${symbol}-EQ`,
    symbol,
    exchange,
    type: "EQUITY",
  };
}

const A = equity("AAA");
const B = equity("BBB");
const C = equity("CCC");
const D = equity("DDD");
const NIFTY: MarketInstrument = {
  instrument_key: "NSE:NSE:NIFTY",
  provider: "fyers",
  provider_token: "NSE:NIFTY",
  symbol: "NIFTY",
  exchange: "NSE",
  type: "INDEX",
};
const RELIANCE = equity("RELIANCE");
const OPTION: MarketInstrument = {
  instrument_key: "NFO:NFO:RELIANCE26OCT1500CE",
  provider: "fyers",
  provider_token: "NFO:RELIANCE26OCT1500CE",
  native_key: "NFO:RELIANCE26OCT1500CE",
  canonical_id: "nse_fo:RELIANCE:2026-10-29:1500:CE",
  symbol: "RELIANCE26OCT1500CE",
  exchange: "NFO",
  type: "OPTION",
  underlying: "RELIANCE",
  expiry: "2026-10-29",
  strike: 1500,
  option_type: "CE",
};

const CHAIN: OptionChainView = {
  underlying: "RELIANCE",
  expiry: "2026-10-29",
  expiries_available: ["2026-10-29", "2026-11-26"],
  spot: 1495,
  spot_basis: "live",
  atm_strike: 1500,
  window: 10,
  strikes_loaded: 1,
  strikes_total_listed: 1,
  rows: [
    {
      strike: 1500,
      atm: true,
      call: {
        instrument_key: "NFO:RELIANCE26OCT1500CE",
        symbol: "RELIANCE26OCT1500CE",
        exchange: "NFO",
        option_type: "CE",
        strike: 1500,
        quote: { ltp: 12.5 },
      },
      put: null,
    },
  ],
  analytics: {},
};

function page(
  results: MarketInstrument[],
  next_cursor: string | null = null,
  total: number | null = results.length,
): BrowseResponse {
  return { count: results.length, results, next_cursor, total };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function Harness({
  onSelect = vi.fn(),
  limit = 2,
}: {
  onSelect?: (row: MarketInstrument) => void;
  limit?: number;
}) {
  const [value, setValue] = useState("");
  return (
    <InstrumentPicker
      value={value}
      onChange={setValue}
      onSelect={onSelect}
      browse
      limit={limit}
      ariaLabel="Search instruments"
    />
  );
}

const options = () => within(screen.getByRole("listbox")).getAllByRole("option");

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("InstrumentPicker browse mode", () => {
  it("loads the first page on open without any query", async () => {
    mockBrowse.mockResolvedValue(page([A, B], "c1", 4));
    render(<Harness />);

    await waitFor(() => expect(options()).toHaveLength(2));
    expect(mockBrowse).toHaveBeenCalledWith(
      expect.objectContaining({
        types: ["INDEX", "EQUITY", "ETF", "FUTURE"],
        limit: 2,
      }),
      expect.anything(),
    );
    expect(mockBrowse.mock.calls[0]?.[0].cursor).toBeUndefined();
    expect(mockSearch).not.toHaveBeenCalled();
    expect(screen.getByText("4 instruments")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load more" })).toBeInTheDocument();
  });

  it("each category requests its own canonical classes from the backend", async () => {
    mockBrowse.mockResolvedValue(page([NIFTY]));
    render(<Harness />);
    await screen.findByRole("option", { name: /NIFTY/ });

    fireEvent.click(screen.getByRole("tab", { name: "Indices" }));
    await waitFor(() =>
      expect(mockBrowse).toHaveBeenLastCalledWith(
        expect.objectContaining({ types: ["INDEX"] }),
        expect.anything(),
      ),
    );

    fireEvent.click(screen.getByRole("tab", { name: "Equities" }));
    await waitFor(() =>
      expect(mockBrowse).toHaveBeenLastCalledWith(
        expect.objectContaining({ types: ["EQUITY"] }),
        expect.anything(),
      ),
    );

    fireEvent.click(screen.getByRole("tab", { name: "ETFs" }));
    await waitFor(() =>
      expect(mockBrowse).toHaveBeenLastCalledWith(
        expect.objectContaining({ types: ["ETF"] }),
        expect.anything(),
      ),
    );
  });

  it("appends cursor pages without duplicates and hides Load more at the end", async () => {
    mockBrowse
      .mockResolvedValueOnce(page([A, B], "c1", 3))
      .mockResolvedValueOnce(page([B, C], null, 3));
    render(<Harness />);
    await screen.findByRole("button", { name: "Load more" });

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));

    await waitFor(() => expect(options()).toHaveLength(3));
    expect(options().map((o) => o.textContent)).toEqual([
      expect.stringContaining("AAA"),
      expect.stringContaining("BBB"),
      expect.stringContaining("CCC"),
    ]);
    expect(mockBrowse).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: "c1", types: ["INDEX", "EQUITY", "ETF", "FUTURE"] }),
      expect.anything(),
    );
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("resets pagination when the category changes mid-page", async () => {
    mockBrowse
      .mockResolvedValueOnce(page([A, B], "c1", 4))
      .mockResolvedValueOnce(page([C, D], "c2", 4))
      .mockResolvedValueOnce(page([NIFTY], null, 1));
    render(<Harness />);
    await screen.findByRole("button", { name: "Load more" });
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(options()).toHaveLength(4));

    fireEvent.click(screen.getByRole("tab", { name: "Indices" }));

    await screen.findByRole("option", { name: /NIFTY/ });
    expect(mockBrowse).toHaveBeenLastCalledWith(
      expect.objectContaining({ types: ["INDEX"] }),
      expect.anything(),
    );
    expect(mockBrowse.mock.calls.at(-1)?.[0].cursor).toBeUndefined();
    expect(options()).toHaveLength(1);
  });

  it("resets pagination when the exchange filter changes", async () => {
    mockBrowse
      .mockResolvedValueOnce(page([A, B], "c1", 4))
      .mockResolvedValueOnce(page([equity("BBB", "BSE")], null, 1));
    render(<Harness />);
    await screen.findByRole("button", { name: "Load more" });

    fireEvent.change(screen.getByLabelText("Filter by exchange"), { target: { value: "BSE" } });

    await waitFor(() =>
      expect(mockBrowse).toHaveBeenLastCalledWith(
        expect.objectContaining({ exchange: "BSE" }),
        expect.anything(),
      ),
    );
    expect(mockBrowse.mock.calls.at(-1)?.[0].cursor).toBeUndefined();
  });

  it("never lets a stale category response overwrite the newer one", async () => {
    const oldPage = deferred<BrowseResponse>();
    const newPage = deferred<BrowseResponse>();
    mockBrowse.mockReturnValueOnce(oldPage.promise).mockReturnValueOnce(newPage.promise);
    render(<Harness />);

    fireEvent.click(screen.getByRole("tab", { name: "Indices" }));
    oldPage.resolve(page([A, B]));
    newPage.resolve(page([NIFTY]));

    await screen.findByRole("option", { name: /NIFTY/ });
    expect(screen.queryByRole("option", { name: /AAA/ })).not.toBeInTheDocument();
    expect(options()).toHaveLength(1);
  });

  it("searches the server beyond the loaded page across the full universe (not category-scoped)", async () => {
    mockBrowse.mockResolvedValue(page([A, B]));
    mockSearch.mockResolvedValue([D]);
    render(<Harness />);
    await screen.findByRole("option", { name: /AAA/ });

    fireEvent.change(screen.getByLabelText("Search instruments"), { target: { value: "DDD" } });

    await screen.findByRole("option", { name: /DDD/ });
    // Category browsing is a navigation filter; a typed query searches the
    // complete supported instrument universe (no category type scope).
    expect(mockSearch).toHaveBeenCalledWith(
      expect.objectContaining({ q: "DDD" }),
      expect.anything(),
    );
    expect(mockSearch.mock.calls.at(-1)?.[0].types).toBeUndefined();
    expect(screen.queryByRole("option", { name: /AAA/ })).not.toBeInTheDocument();
  });

  it("clearing the search returns to the current browse category", async () => {
    mockBrowse.mockResolvedValue(page([NIFTY]));
    mockSearch.mockResolvedValue([D]);
    render(<Harness />);
    await screen.findByRole("option", { name: /NIFTY/ });
    fireEvent.click(screen.getByRole("tab", { name: "Indices" }));
    await waitFor(() => expect(mockBrowse).toHaveBeenCalledTimes(2));

    const input = screen.getByLabelText("Search instruments");
    fireEvent.change(input, { target: { value: "DDD" } });
    await screen.findByRole("option", { name: /DDD/ });
    fireEvent.change(input, { target: { value: "" } });

    await screen.findByRole("option", { name: /NIFTY/ });
    expect(mockBrowse).toHaveBeenLastCalledWith(
      expect.objectContaining({ types: ["INDEX"] }),
      expect.anything(),
    );
    expect(mockBrowse.mock.calls.at(-1)?.[0].cursor).toBeUndefined();
  });

  it("renders loading, empty (total null) and error-with-retry states", async () => {
    const pending = deferred<BrowseResponse>();
    mockBrowse.mockReturnValueOnce(pending.promise);
    const { unmount } = render(<Harness />);
    expect(screen.getByText("Loading instruments…")).toBeInTheDocument();
    pending.resolve(page([]));
    await screen.findByText("No instruments in this category.");
    // Phase 1 limitation L1: an empty first page reports total null.
    expect(screen.getByText("0 instruments")).toBeInTheDocument();
    unmount();

    mockBrowse.mockRejectedValueOnce(new Error("Catalog offline")).mockResolvedValueOnce(page([A]));
    render(<Harness />);
    expect(await screen.findByText("Catalog offline")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByRole("option", { name: /AAA/ });
  });

  it("keeps loaded rows when a continuation page fails and retries the cursor", async () => {
    mockBrowse
      .mockResolvedValueOnce(page([A, B], "c1", 3))
      .mockRejectedValueOnce(new Error("Page failed"))
      .mockResolvedValueOnce(page([C], null, 3));
    render(<Harness />);
    await screen.findByRole("button", { name: "Load more" });
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByText("Page failed")).toBeInTheDocument();
    expect(options()).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(options()).toHaveLength(3));
    expect(mockBrowse).toHaveBeenLastCalledWith(
      expect.objectContaining({ cursor: "c1" }),
      expect.anything(),
    );
  });

  it("selects a browse row with the keyboard and keeps the list open", async () => {
    mockBrowse.mockResolvedValue(page([A, B]));
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);
    await screen.findByRole("option", { name: /AAA/ });

    const input = screen.getByLabelText("Search instruments");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onSelect).toHaveBeenCalledWith(B);
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(input).toHaveValue("");
  });

  it("navigates F&O underlying → expiry → strike → CE/PE with exact identity", async () => {
    mockBrowse.mockResolvedValue(page([RELIANCE, NIFTY]));
    mockChain.mockResolvedValue(CHAIN);
    mockResolve.mockResolvedValue(OPTION);
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);

    fireEvent.click(screen.getByRole("tab", { name: "F&O" }));
    await waitFor(() =>
      expect(mockBrowse).toHaveBeenLastCalledWith(
        expect.objectContaining({ types: ["EQUITY", "INDEX"] }),
        expect.anything(),
      ),
    );
    // Options are never requested unscoped.
    for (const call of mockBrowse.mock.calls) {
      expect(call[0].types).not.toContain("OPTION");
    }

    fireEvent.click(await screen.findByRole("option", { name: /RELIANCE/ }));
    await waitFor(() =>
      expect(mockChain).toHaveBeenCalledWith(
        expect.objectContaining({ underlying: "RELIANCE", window: 10 }),
        expect.anything(),
      ),
    );

    fireEvent.change(await screen.findByLabelText("Option expiry"), {
      target: { value: "2026-11-26" },
    });
    await waitFor(() =>
      expect(mockChain).toHaveBeenLastCalledWith(
        expect.objectContaining({ underlying: "RELIANCE", expiry: "2026-11-26" }),
        expect.anything(),
      ),
    );

    fireEvent.click(await screen.findByRole("button", { name: "RELIANCE26OCT1500CE CE" }));

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(OPTION));
    expect(mockResolve).toHaveBeenCalledWith("RELIANCE26OCT1500CE", {
      exchange: "NFO",
      type: "OPTION",
    });
    // The chain stays open for another leg; the query is not replaced.
    expect(screen.getByRole("button", { name: "RELIANCE26OCT1500CE CE" })).toBeInTheDocument();
    expect(screen.getByLabelText("Search instruments")).toHaveValue("RELIANCE");
  });

  it("global search finds NIFTY index from the Equities tab", async () => {
    mockBrowse.mockResolvedValue(page([A]));
    mockSearch.mockResolvedValue([NIFTY]);
    render(<Harness />);
    await screen.findByRole("option", { name: /AAA/ });
    fireEvent.click(screen.getByRole("tab", { name: "Equities" }));
    await waitFor(() =>
      expect(mockBrowse).toHaveBeenLastCalledWith(
        expect.objectContaining({ types: ["EQUITY"] }),
        expect.anything(),
      ),
    );

    fireEvent.change(screen.getByLabelText("Search instruments"), { target: { value: "NIFTY" } });
    await screen.findByRole("option", { name: /NIFTY/ });
    expect(mockSearch).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: "NIFTY" }),
      expect.anything(),
    );
    expect(mockSearch.mock.calls.at(-1)?.[0].types).toBeUndefined();
  });

  it("global search finds RELIANCE equity from the Indices tab", async () => {
    mockBrowse.mockResolvedValue(page([NIFTY]));
    mockSearch.mockResolvedValue([RELIANCE]);
    render(<Harness />);
    await screen.findByRole("option", { name: /NIFTY/ });
    fireEvent.click(screen.getByRole("tab", { name: "Indices" }));
    await waitFor(() =>
      expect(mockBrowse).toHaveBeenLastCalledWith(
        expect.objectContaining({ types: ["INDEX"] }),
        expect.anything(),
      ),
    );

    fireEvent.change(screen.getByLabelText("Search instruments"), {
      target: { value: "RELIANCE" },
    });
    await screen.findByRole("option", { name: /RELIANCE/ });
    expect(mockSearch.mock.calls.at(-1)?.[0].types).toBeUndefined();
  });

  it("global search finds a future from the Equities tab", async () => {
    const FUT: MarketInstrument = {
      instrument_key: "NFO:NFO:RELIANCE26OCTFUT",
      provider: "fyers",
      provider_token: "NFO:RELIANCE26OCTFUT",
      symbol: "RELIANCE26OCTFUT",
      exchange: "NFO",
      type: "FUTURE",
      underlying: "RELIANCE",
      expiry: "2026-10-29",
    };
    mockBrowse.mockResolvedValue(page([A]));
    mockSearch.mockResolvedValue([FUT]);
    render(<Harness />);
    await screen.findByRole("option", { name: /AAA/ });
    fireEvent.click(screen.getByRole("tab", { name: "Equities" }));
    await waitFor(() => expect(mockBrowse).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("Search instruments"), {
      target: { value: "RELIANCE FUT" },
    });
    await screen.findByRole("option", { name: /RELIANCE26OCTFUT/ });
    expect(mockSearch.mock.calls.at(-1)?.[0].types).toBeUndefined();
  });

  it("preserves exchange filter during global search", async () => {
    mockBrowse.mockResolvedValue(page([A]));
    mockSearch.mockResolvedValue([RELIANCE]);
    render(<Harness />);
    await screen.findByRole("option", { name: /AAA/ });
    fireEvent.change(screen.getByLabelText("Filter by exchange"), { target: { value: "NSE" } });
    await waitFor(() => expect(mockBrowse).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("Search instruments"), {
      target: { value: "RELIANCE" },
    });
    await screen.findByRole("option", { name: /RELIANCE/ });
    expect(mockSearch).toHaveBeenLastCalledWith(
      expect.objectContaining({ q: "RELIANCE", exchange: "NSE" }),
      expect.anything(),
    );
    expect(mockSearch.mock.calls.at(-1)?.[0].types).toBeUndefined();
  });

  it("clearing global search restores the original category browse state", async () => {
    mockBrowse.mockResolvedValue(page([A]));
    mockSearch.mockResolvedValue([NIFTY]);
    render(<Harness />);
    await screen.findByRole("option", { name: /AAA/ });
    fireEvent.click(screen.getByRole("tab", { name: "Equities" }));
    await waitFor(() =>
      expect(mockBrowse).toHaveBeenLastCalledWith(
        expect.objectContaining({ types: ["EQUITY"] }),
        expect.anything(),
      ),
    );
    const callsBefore = mockBrowse.mock.calls.length;

    const input = screen.getByLabelText("Search instruments");
    fireEvent.change(input, { target: { value: "NIFTY" } });
    await screen.findByRole("option", { name: /NIFTY/ });
    fireEvent.change(input, { target: { value: "" } });

    await waitFor(() => expect(mockBrowse.mock.calls.length).toBeGreaterThan(callsBefore));
    expect(mockBrowse).toHaveBeenLastCalledWith(
      expect.objectContaining({ types: ["EQUITY"] }),
      expect.anything(),
    );
    expect(mockBrowse.mock.calls.at(-1)?.[0].cursor).toBeUndefined();
  });
});
