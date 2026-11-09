import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/market", () => ({
  searchMarketInstruments: vi.fn(),
  browseInstruments: vi.fn(),
  getOptionChainView: vi.fn(),
  resolveMarketInstrument: vi.fn(),
}));

import { getOptionChainView, resolveMarketInstrument, searchMarketInstruments } from "@/api/market";
import { InstrumentPicker } from "@/components/InstrumentPicker";
import type { MarketInstrument, OptionChainView } from "@/types";

const RELIANCE: MarketInstrument = {
  instrument_key: "NSE:NSE_EQ|INE002A01018",
  provider: "upstox",
  provider_token: "NSE_EQ|INE002A01018",
  symbol: "RELIANCE",
  exchange: "NSE",
  type: "EQUITY",
};

const INFY: MarketInstrument = {
  instrument_key: "NSE:NSE:INFY-EQ",
  provider: "fyers",
  provider_token: "NSE:INFY-EQ",
  symbol: "INFY",
  exchange: "NSE",
  type: "EQUITY",
};

const OPTION: MarketInstrument = {
  instrument_key: "NFO:NFO:RELIANCE26OCT1500CE",
  provider: "fyers",
  provider_token: "NFO:RELIANCE26OCT1500CE",
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

const mockSearch = searchMarketInstruments as ReturnType<typeof vi.fn>;
const mockChain = getOptionChainView as ReturnType<typeof vi.fn>;
const mockResolve = resolveMarketInstrument as ReturnType<typeof vi.fn>;

function Harness({
  onSelect,
  fno = true,
}: {
  onSelect: (row: MarketInstrument) => void;
  fno?: boolean;
}) {
  const [value, setValue] = useState("");
  return (
    <InstrumentPicker
      value={value}
      onChange={setValue}
      onSelect={onSelect}
      fno={fno}
      ariaLabel="Search instruments"
    />
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("InstrumentPicker", () => {
  it("renders ranked canonical results with provider attribution and selects", async () => {
    mockSearch.mockResolvedValue([RELIANCE, INFY]);
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);

    fireEvent.change(screen.getByLabelText("Search instruments"), { target: { value: "REL" } });

    const listbox = screen.getByRole("listbox");
    const options = await within(listbox).findAllByRole("option");
    expect(options).toHaveLength(2);
    expect(options[0]).toHaveTextContent("RELIANCE");
    expect(options[0]).toHaveTextContent("upstox");
    expect(mockSearch).toHaveBeenCalledWith(
      expect.objectContaining({ q: "REL", types: undefined, limit: 15 }),
      expect.anything(),
    );

    fireEvent.click(options[0] as HTMLElement);
    expect(onSelect).toHaveBeenCalledWith(RELIANCE);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("moves with the keyboard and selects the active row with Enter", async () => {
    mockSearch.mockResolvedValue([RELIANCE, INFY]);
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);
    const input = screen.getByLabelText("Search instruments");

    fireEvent.change(input, { target: { value: "REL" } });
    await within(screen.getByRole("listbox")).findAllByRole("option");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onSelect).toHaveBeenCalledWith(INFY);
  });

  it("closes on Escape and on an outside pointer press", async () => {
    mockSearch.mockResolvedValue([RELIANCE]);
    render(<Harness onSelect={vi.fn()} />);
    const input = screen.getByLabelText("Search instruments");

    fireEvent.change(input, { target: { value: "REL" } });
    await within(screen.getByRole("listbox")).findAllByRole("option");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    fireEvent.focus(input);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("aborts the debounced search on unmount", () => {
    vi.useFakeTimers();
    mockSearch.mockResolvedValue([RELIANCE]);
    const { unmount } = render(<Harness onSelect={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("Search instruments"), { target: { value: "REL" } });
    unmount();
    vi.runAllTimers();

    expect(mockSearch).not.toHaveBeenCalled();
  });

  it("shows the error state", async () => {
    mockSearch.mockRejectedValue(new Error("Search unavailable"));
    render(<Harness onSelect={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("Search instruments"), { target: { value: "REL" } });

    expect(await screen.findByText("Search unavailable")).toBeInTheDocument();
  });

  it("shows the empty state", async () => {
    mockSearch.mockResolvedValue([]);
    render(<Harness onSelect={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("Search instruments"), { target: { value: "ZZZ" } });

    expect(await screen.findByText("No matching instruments.")).toBeInTheDocument();
  });

  it("sends the type filter to the ranked search", async () => {
    mockSearch.mockResolvedValue([RELIANCE]);
    render(<Harness onSelect={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("Search instruments"), { target: { value: "REL" } });
    await within(screen.getByRole("listbox")).findAllByRole("option");

    fireEvent.change(screen.getByLabelText("Filter by instrument type"), {
      target: { value: "OPTION" },
    });

    await waitFor(() =>
      expect(mockSearch).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: "REL", types: ["OPTION"] }),
        expect.anything(),
      ),
    );
  });

  it("selects an option leg through the structured F&O mode", async () => {
    mockSearch.mockResolvedValue([RELIANCE]);
    mockChain.mockResolvedValue(CHAIN);
    mockResolve.mockResolvedValue(OPTION);
    const onSelect = vi.fn();
    render(<Harness onSelect={onSelect} />);

    fireEvent.click(screen.getByRole("button", { name: "F&O" }));
    fireEvent.change(screen.getByLabelText("Search instruments"), { target: { value: "RELI" } });
    fireEvent.click(
      await within(screen.getByRole("listbox")).findByRole("option", { name: /RELIANCE/ }),
    );

    expect(mockSearch).toHaveBeenCalledWith(
      expect.objectContaining({ q: "RELI", types: ["EQUITY", "INDEX"] }),
      expect.anything(),
    );
    await waitFor(() =>
      expect(mockChain).toHaveBeenCalledWith(
        expect.objectContaining({ underlying: "RELIANCE", window: 10 }),
        expect.anything(),
      ),
    );

    fireEvent.click(await screen.findByRole("button", { name: "RELIANCE26OCT1500CE CE" }));

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(OPTION));
    expect(mockResolve).toHaveBeenCalledWith("RELIANCE26OCT1500CE", {
      exchange: "NFO",
      type: "OPTION",
    });
  });
});
