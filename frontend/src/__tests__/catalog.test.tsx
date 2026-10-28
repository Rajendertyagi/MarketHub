import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/market", () => ({
  searchMarketInstruments: vi.fn(),
  getOptionChainView: vi.fn(),
  resolveMarketInstrument: vi.fn(),
}));

import { searchMarketInstruments } from "@/api/market";
import { CatalogView } from "@/features/catalog/CatalogView";
import type { MarketInstrument } from "@/types";

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

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("CatalogView", () => {
  it("starts empty and asks for a query", () => {
    renderCatalog();
    expect(screen.getByText(/Type a symbol to browse the canonical catalog/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
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

  it("returns focus to the search input after picking a result", async () => {
    renderCatalog();
    await pickRelianceOption();
    expect(searchBox()).toHaveFocus();
  });

  it("clears the selection back to the empty state", async () => {
    renderCatalog();
    await pickRelianceOption();
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText(/Type a symbol to browse the canonical catalog/)).toBeInTheDocument();
  });
});
