import { useEffect } from "react";
import { fmtIv, fmtNum, fmtVol } from "@/utils/format";
import { useQuoteDepth } from "../useQuoteDepth";
import type { MarketQuote } from "../types";

interface Props {
  quote: MarketQuote | null;
  onClose: () => void;
}

function kv(label: string, value: string) {
  return (
    <tr>
      <td>{label}</td>
      <td>{value}</td>
    </tr>
  );
}

export function QuoteDrawer({ quote, onClose }: Props) {
  useEffect(() => {
    if (!quote) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [quote, onClose]);

  const depth = useQuoteDepth(
    quote?.exchange ?? null,
    quote?.instrument_token ?? null,
  );

  if (!quote) return null;

  const gk = quote.greeks;
  const depthLevels = (levels: { price: number; quantity: number; orders?: number | null }[]) =>
    levels
      .map((l) => (
        <tr key={`${l.price}-${l.quantity}`}>
          <td>{fmtNum(l.price)}</td>
          <td>{fmtNum(l.quantity, 0)}</td>
          <td>{l.orders != null ? fmtNum(l.orders, 0) : "—"}</td>
        </tr>
      ));

  return (
    <div className="drawer" role="dialog" aria-label="Quote details">
      <div className="drawer-header">
        <span className="mono">
          {quote.tradingsymbol ?? quote.instrument_token} · {quote.exchange}
        </span>
        <button className="icon-btn" title="Close" onClick={onClose}>
          ✕
        </button>
      </div>
      <div className="drawer-body">
        <h3>Price</h3>
        <table className="data-table kv-table">
          <tbody>
            {kv("LTP", quote.ltp != null ? fmtNum(quote.ltp) : "—")}
            {kv("Prev Close", quote.close != null ? fmtNum(quote.close) : "—")}
            {kv("Change", quote.change != null ? fmtNum(quote.change) : "—")}
            {kv("Change %", quote.change_percent != null ? `${fmtNum(quote.change_percent)}%` : "—")}
            {kv("Open", quote.open != null ? fmtNum(quote.open) : "—")}
            {kv("High", quote.high != null ? fmtNum(quote.high) : "—")}
            {kv("Low", quote.low != null ? fmtNum(quote.low) : "—")}
            {kv("ATP", quote.avg_trade_price != null ? fmtNum(quote.avg_trade_price) : "—")}
          </tbody>
        </table>

        <h3>Trade / Volume</h3>
        <table className="data-table kv-table">
          <tbody>
            {kv("Last Trade Qty", fmtNum(quote.last_traded_qty, 0))}
            {kv("Last Trade Time", quote.last_trade_time ?? "—")}
            {kv("Volume", fmtVol(quote.volume))}
            {kv("Total Buy Qty", fmtVol(quote.total_buy_qty))}
            {kv("Total Sell Qty", fmtVol(quote.total_sell_qty))}
          </tbody>
        </table>

        <h3>Open Interest</h3>
        <table className="data-table kv-table">
          <tbody>
            {kv("OI", fmtVol(quote.open_interest))}
            {kv("Previous OI", fmtVol(quote.previous_oi))}
            {kv("OI Change", fmtNum(quote.oi_change, 0))}
            {kv("OI Change %", quote.oi_change_percent != null ? `${fmtNum(quote.oi_change_percent)}%` : "—")}
          </tbody>
        </table>

        <h3>Market</h3>
        <table className="data-table kv-table">
          <tbody>
            {kv("Bid", quote.best_bid != null ? fmtNum(quote.best_bid) : "—")}
            {kv("Ask", quote.best_ask != null ? fmtNum(quote.best_ask) : "—")}
            {kv("Upper Circuit", quote.upper_circuit != null ? fmtNum(quote.upper_circuit) : "—")}
            {kv("Lower Circuit", quote.lower_circuit != null ? fmtNum(quote.lower_circuit) : "—")}
            {kv("Exchange Time", quote.exchange_ts ?? "—")}
            {kv("Received", quote.received_ts ?? "—")}
          </tbody>
        </table>

        <h3>Option Greeks</h3>
        {gk && Object.values(gk).some((v) => v != null) ? (
          <table className="data-table kv-table">
            <tbody>
              {kv("Delta", gk.delta != null ? gk.delta.toFixed(4) : "—")}
              {kv("Gamma", gk.gamma != null ? gk.gamma.toFixed(6) : "—")}
              {kv("Theta", gk.theta != null ? gk.theta.toFixed(4) : "—")}
              {kv("Vega", gk.vega != null ? gk.vega.toFixed(4) : "—")}
              {kv("Rho", gk.rho != null ? gk.rho.toFixed(4) : "—")}
              {kv("IV", gk.iv != null ? fmtIv(gk.iv) : "—")}
            </tbody>
          </table>
        ) : (
          <em>Greeks not available</em>
        )}

        <h3>Depth</h3>
        {depth.isLoading ? (
          <em>Loading…</em>
        ) : depth.isError ? (
          <em>Depth unavailable</em>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Bid Px</th>
                <th>Qty</th>
                <th>Ord</th>
                <th>Ask Px</th>
                <th>Qty</th>
                <th>Ord</th>
              </tr>
            </thead>
            <tbody>
              {depthLevels(depth.data?.bids ?? [])}
              {depthLevels(depth.data?.asks ?? [])}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
