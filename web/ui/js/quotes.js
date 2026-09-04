/**
 * MarketHub WebUI — quote details drawer.
 *
 * Slide-out instrument detail (price/trade/OI/greeks/depth). Reads live
 * quotes via market.js; refreshes open content on ticks (5s interval
 * owned here, created once).
 */

import { $, fmt, fmtNum, fmtTs } from "./utils.js";
import { getQuote, quotes } from "./market.js";

const kvRow = (label, value) =>
  `<tr><td>${label}</td><td>${value ?? "—"}</td></tr>`;

let _drawerIntervalStarted = false;

function renderDrawer(key) {
  const q = getQuote(key);
  if (!q) return;
  $("drawer-title").textContent =
    (q.tradingsymbol || key.split(":").pop()) + "  ·  " + (q.exchange || "");

  $("drawer-price").innerHTML =
    kvRow("LTP", q.ltp != null ? fmt(q.ltp) : "—") +
    kvRow("Prev Close", q.close != null ? fmt(q.close) : "—") +
    kvRow("Change", q.change != null ? fmt(q.change) : "—") +
    kvRow("Change %", q.change_percent != null ? fmt(q.change_percent) + "%" : "—") +
    kvRow("Open", q.open != null ? fmt(q.open) : "—") +
    kvRow("High", q.high != null ? fmt(q.high) : "—") +
    kvRow("Low", q.low != null ? fmt(q.low) : "—") +
    kvRow("ATP", q.avg_trade_price != null ? fmt(q.avg_trade_price) : "—");

  $("drawer-trade").innerHTML =
    kvRow("Last Trade Qty", fmtNum(q.last_traded_qty)) +
    kvRow("Last Trade Time", fmtTs(q.last_trade_time)) +
    kvRow("Volume", fmtNum(q.volume)) +
    kvRow("Total Buy Qty", fmtNum(q.total_buy_qty)) +
    kvRow("Total Sell Qty", fmtNum(q.total_sell_qty));

  $("drawer-oi").innerHTML =
    kvRow("OI", fmtNum(q.open_interest)) +
    kvRow("Previous OI", fmtNum(q.previous_oi)) +
    kvRow("OI Change", fmtNum(q.oi_change)) +
    kvRow("OI Change %",
          q.oi_change_percent != null ? fmt(q.oi_change_percent) + "%" : "—");

  $("drawer-markets-section").innerHTML =
    kvRow("Bid", q.best_bid != null ? fmt(q.best_bid) : "—") +
    kvRow("Ask", q.best_ask != null ? fmt(q.best_ask) : "—") +
    kvRow("Upper Circuit", q.upper_circuit != null ? fmt(q.upper_circuit) : "—") +
    kvRow("Lower Circuit", q.lower_circuit != null ? fmt(q.lower_circuit) : "—") +
    kvRow("Exchange Time", fmtTs(q.exchange_ts)) +
    kvRow("Received", fmtTs(q.received_ts));

  const gk = q.greeks;
  if (gk && Object.values(gk).some((v) => v != null)) {
    $("drawer-greeks").innerHTML =
      `<table class="data-table kv-table">` +
      kvRow("Delta", gk.delta != null ? gk.delta.toFixed(4) : "—") +
      kvRow("Gamma", gk.gamma != null ? gk.gamma.toFixed(6) : "—") +
      kvRow("Theta", gk.theta != null ? gk.theta.toFixed(4) : "—") +
      kvRow("Vega", gk.vega != null ? gk.vega.toFixed(4) : "—") +
      kvRow("Rho", gk.rho != null ? gk.rho.toFixed(4) : "—") +
      kvRow("IV", gk.iv != null ? fmt(gk.iv) + "%" : "—") +
      `</table>`;
  } else {
    $("drawer-greeks").innerHTML = "<em>Greeks not available</em>";
  }

  // Depth fetched on demand — no continuous polling.
  $("drawer-depth").innerHTML = "<em>Loading…</em>";
  fetch(`/api/market/depth/${encodeURIComponent(q.exchange)}/${encodeURIComponent(q.instrument_token)}`)
    .then((r) => r.json())
    .then((d) => {
      const lvlRows = (levels) => levels.map((l) =>
        `<tr><td>${fmt(l.price)}</td><td>${fmtNum(l.quantity)}</td>` +
        `<td>${l.orders != null ? l.orders : "—"}</td></tr>`).join("");
      $("drawer-depth").innerHTML =
        `<table class="data-table"><thead><tr>` +
        `<th>Bid Px</th><th>Qty</th><th>Ord</th>` +
        `<th>Ask Px</th><th>Qty</th><th>Ord</th></tr></thead><tbody>` +
        lvlRows(d.bids || []) + lvlRows(d.asks || []) +
        `</tbody></table>`;
    })
    .catch(() => { $("drawer-depth").innerHTML = "<em>Depth unavailable</em>"; });
}

function openDrawer(key) {
  renderDrawer(key);
  $("quote-drawer").classList.remove("hidden");
}
function closeDrawer() {
  $("quote-drawer").classList.add("hidden");
}

export function initDrawer() {
  $("drawer-close").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrawer();
  });
  for (const bodyId of ["dash-body", "markets-body"]) {
    const body = $(bodyId);
    if (body) {
      body.addEventListener("click", (e) => {
        const row = e.target.closest("tr[data-key]");
        if (row) openDrawer(row.dataset.key);
      });
    }
  }
  // Refresh drawer content on live ticks while open.
  if (_drawerIntervalStarted) return;
  _drawerIntervalStarted = true;
  setInterval(() => {
    const title = $("drawer-title").textContent;
    if ($("quote-drawer").classList.contains("hidden") || title === "—") return;
    for (const [key] of quotes) {
      if (title.startsWith(key.split("|").pop().split(":").pop())) {
        renderDrawer(key);
        break;
      }
    }
  }, 5000);
}
