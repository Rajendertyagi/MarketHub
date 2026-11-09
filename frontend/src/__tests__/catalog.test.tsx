import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/market", () => ({
  searchMarketInstruments: vi.fn(),
  browseInstruments: vi.fn(),
  getOptionChainView: vi.fn(),
  resolveMarketInstrument: vi.fn(),
}));

import { browseInstruments, searchMarketInstruments } from "@/api/market";
import { CatalogView } from "@/features/catalog/CatalogView";
import type { BrowseResponse, MarketInstrument } from "@/types";

const RELIANCE: MarketInstrument = {
  instrument_key: "NSE:NSE:RELIANCE-EQ",
  provider: "fyers",
  provider_token: "NSE:RELIANCE-EQ",
  native_key: "NSE:RELIANCE-EQ",
  canonical_id: "nse_eq:RELIANCE",
  symbol: "RELIANCE",
  display_name: "Reliance Industries",
  exchange: "NSE",
  type: "EQUITY",
  lot_size: 1,
};

const NIFTY: MarketInstrument = {
  instrument_key: "NSE:NSE:NIFTY",
  provider: "fyers",
  provider_token: "NSE:NIFTY",
  symbol: "NIFTY",
  display_name: "Nifty 50",
  exchange: "NSE",
  type: "INDEX",
};

const OPTION: MarketInstrument = {
  instrument_key: "NFO:NFO:RELIANCE26OCT1500CE",
  provider: "fyers",
  provider_token: "NFO:RELIANCE26OCT1500CE",
  native_key: "NFO:RELIANCE26OCT1500CE",
  canonical_id: "nse_fo:RELIANCE:2026-10-29:1500:CE",
  symbol: "RELIANCE26OCT1500CE",
  display_name: "RELIANCE 1500 CE 29 Oct",
  exchange: "NFO",
  type: "OPTION",
  underlying: "RELIANCE",
  expiry: "2026-10-29",
  strike: 1500,
  option_type: "CE",
  lot_size: 500,
};

const mockSearch = searchMarketInstruments as ReturnType<typeof vi.fn>;
const mockBrowse = browseInstruments as ReturnType<typeof vi.fn>;

function page(results: MarketInstrument[]): BrowseResponse {
  return { count: results.length, results, next_cursor: null, total: results.length };
}

function ChartProbe() {
  const loc = useLocation();
  return <div data-testid="chart-probe">{`${loc.pathname}${loc.search}`}</div>;
}

function renderCatalog() {
  return render(
    <MemoryRouter initialEntries={["/catalog"]}>
      <Routes>
        <Route path="/catalog" element={<CatalogView />} />
        <Route path="/chart" element={<ChartProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

const searchBox = () => screen.getByRole("combobox", { name: "Search the instrument catalog" });

async function pickRelianceOption() {
  mockSearch.mockResolvedValue([OPTION]);
  fireEvent.change(searchBox(), { target: { value: "RELIANCE 1500 CE" } });
  fireEvent.click(await screen.findByRole("option", { name: /RELIANCE26OCT1500CE/ }));
}

beforeEach(() => {
  mockBrowse.mockResolvedValue(page([]));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("CatalogView", () => {
  it("opens browse-first: instruments load without a search query", async () => {
    mockBrowse.mockResolvedValue(page([NIFTY, RELIANCE]));
    renderCatalog();

    expect(await screen.findByRole("option", { name: /NIFTY/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /RELIANCE/ })).toBeInTheDocument();
    expect(mockBrowse).toHaveBeenCalledWith(
      expect.objectContaining({ types: ["INDEX", "EQUITY", "ETF", "FUTURE"], limit: 50 }),
      expect.anything(),
    );
    expect(mockSearch).not.toHaveBeenCalled();
    expect(screen.getByRole("tab", { name: "All" })).toHaveAttribute("aria-selected", "true");
  });

  it("switches category through the backend and resets the page", async () => {
    mockBrowse.mockImplementation((params: { types?: string[] }) =>
      Promise.resolve(
        page(
          params.types?.length === 1 && params.types[0] === "INDEX" ? [NIFTY] : [NIFTY, RELIANCE],
        ),
      ),
    );
    renderCatalog();
    await screen.findByRole("option", { name: /RELIANCE/ });

    fireEvent.click(screen.getByRole("tab", { name: "Indices" }));

    await waitFor(() =>
      expect(mockBrowse).toHaveBeenLastCalledWith(
        expect.objectContaining({ types: ["INDEX"] }),
        expect.anything(),
      ),
    );
    expect(mockBrowse.mock.calls.at(-1)?.[0].cursor).toBeUndefined();
    await waitFor(() =>
      expect(screen.queryByRole("option", { name: /^RELIANCE/ })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("tab", { name: "Indices" })).toHaveAttribute("aria-selected", "true");
  });

  it("renders the selected row from the compact canonical fields", async () => {
    renderCatalog();
    await pickRelianceOption();

    const table = screen.getByRole("table", { name: "Selected instrument" });
    expect(within(table).getByRole("rowheader", { name: "Symbol" })).toBeInTheDocument();
    expect(within(table).getByText("RELIANCE26OCT1500CE")).toBeInTheDocument();
    expect(within(table).getByText("RELIANCE 1500 CE 29 Oct")).toBeInTheDocument();
    expect(within(table).getByText("NFO")).toBeInTheDocument();
    expect(within(table).getByText("2026-10-29")).toBeInTheDocument();
    expect(within(table).getByText("1500")).toBeInTheDocument();
    expect(within(table).getByText("CE")).toBeInTheDocument();
    expect(within(table).getByText("500")).toBeInTheDocument();
    expect(within(table).getByText("NFO:NFO:RELIANCE26OCT1500CE")).toBeInTheDocument();
  });

  it("opens the chart with the bare provider token and owning provider", async () => {
    renderCatalog();
    await pickRelianceOption();

    fireEvent.click(screen.getByRole("button", { name: "Open chart" }));

    const probe = await screen.findByTestId("chart-probe");
    const [path, search] = (probe.textContent ?? "").split("?");
    expect(path).toBe("/chart");
    const params = new URLSearchParams(search);
    expect(params.get("key")).toBe("NFO:RELIANCE26OCT1500CE");
    expect(params.get("sym")).toBe("RELIANCE26OCT1500CE");
    expect(params.get("ex")).toBe("NFO");
    expect(params.get("type")).toBe("OPTION");
    expect(params.get("prov")).toBe("fyers");
    // The composite display key must never travel as the token.
    expect(params.get("key")).not.toBe(OPTION.instrument_key);
  });

  it("searches beyond the loaded browse page and clears back to the category", async () => {
    mockBrowse.mockResolvedValue(page([NIFTY]));
    renderCatalog();
    await screen.findByRole("option", { name: /NIFTY/ });

    mockSearch.mockResolvedValue([RELIANCE]);
    fireEvent.change(searchBox(), { target: { value: "RELIANCE" } });
    expect(await screen.findByRole("option", { name: /RELIANCE/ })).toBeInTheDocument();
    expect(mockSearch).toHaveBeenCalledWith(
      expect.objectContaining({ q: "RELIANCE" }),
      expect.anything(),
    );
    expect(mockSearch.mock.calls.at(-1)?.[0].types).toBeUndefined();
    expect(screen.queryByRole("option", { name: /NIFTY/ })).not.toBeInTheDocument();

    fireEvent.change(searchBox(), { target: { value: "" } });
    expect(await screen.findByRole("option", { name: /NIFTY/ })).toBeInTheDocument();
  });

  it("applies the exchange filter on the backend", async () => {
    mockBrowse.mockResolvedValue(page([NIFTY]));
    renderCatalog();
    await screen.findByRole("option", { name: /NIFTY/ });

    fireEvent.change(screen.getByLabelText("Filter by exchange"), { target: { value: "BSE" } });

    await waitFor(() =>
      expect(mockBrowse).toHaveBeenLastCalledWith(
        expect.objectContaining({ exchange: "BSE" }),
        expect.anything(),
      ),
    );
  });

  it("clears the selection while keeping the browse list available", async () => {
    mockBrowse.mockResolvedValue(page([RELIANCE]));
    renderCatalog();
    fireEvent.click(await screen.findByRole("option", { name: /RELIANCE/ }));
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(
      screen.getByText(/Pick an instrument above to inspect its canonical identity/),
    ).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /RELIANCE/ })).toBeInTheDocument();
  });
});
