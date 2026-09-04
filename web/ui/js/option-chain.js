/**
 * MarketHub WebUI — option chain.
 *
 * Owns underlying search, expiry loading, chain fetch and the ATM-aware
 * strike table. Selection state is module-local (declared — never
 * implicit globals).
 */

import { $, fmt, fmtNum, fmtVol } from "./utils.js";

let ocUnderlying = null;
let ocFullStrikes = [];

export function initOptionChain() {
  const search = $("oc-underlying-search");
  const sel = $("oc-underlying-select");
  const expSel = $("oc-expiry-select");
  const msg = $("oc-message");

  let debounce = null;
  search.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(async () => {
      const q = search.value.trim();
      if (!q) return;
      try {
        const res = await fetch("/api/options/underlyings?q=" +
          encodeURIComponent(q));
        const d = await res.json();
        sel.innerHTML = '<option value="">Underlying…</option>' +
          (d.underlyings || []).map((u) =>
            `<option value="${u}">${u}</option>`).join("");
      } catch { /* silent */ }
    }, 300);
  });

  sel.addEventListener("change", async () => {
    ocUnderlying = sel.value;
    expSel.innerHTML = '<option value="">Expiry…</option>';
    expSel.disabled = true;
    if (!ocUnderlying) return;
    try {
      const res = await fetch("/api/options/expiries?underlying=" +
        encodeURIComponent(ocUnderlying));
      const d = await res.json();
      expSel.innerHTML = '<option value="">Expiry…</option>' +
        (d.expiries || []).map((e) =>
          `<option value="${e}">${e}</option>`).join("");
      expSel.disabled = !(d.expiries || []).length;
    } catch { /* silent */ }
  });

  $("oc-load").addEventListener("click", async () => {
    const expiry = expSel.value;
    msg.textContent = "";
    msg.className = "hint";
    if (!ocUnderlying || !expiry) {
      msg.textContent = "Pick an underlying and expiry first.";
      msg.className = "hint err";
      return;
    }
    // Resolve the underlying's instrument key from catalog search.
    try {
      const sres = await fetch("/api/instruments/search?q=" +
        encodeURIComponent(ocUnderlying) + "&limit=5");
      const sd = await sres.json();
      const hit = (sd.results || []).find(
        (r) => r.name === ocUnderlying || r.tradingsymbol === ocUnderlying)
        || (sd.results || [])[0];
      if (!hit) {
        msg.textContent = "Underlying not found in catalog.";
        msg.className = "hint err";
        return;
      }
      const res = await fetch("/api/options/chain?instrument_key=" +
        encodeURIComponent(hit.instrument_token) + "&exchange=" +
        encodeURIComponent(hit.exchange) + "&tradingsymbol=" +
        encodeURIComponent(hit.tradingsymbol) + "&expiry=" + expiry);
      const d = await res.json();
      if (!res.ok) {
        msg.textContent = d.error || "Chain load failed.";
        msg.className = "hint err";
        return;
      }
      $("oc-spot").textContent = d.spot_price != null
        ? fmt(d.spot_price) : "—";
      $("oc-atm").textContent = d.atm_strike != null
        ? fmt(d.atm_strike) : "—";
      ocFullStrikes = d.strikes || [];
      renderOcStrikes();
    } catch {
      msg.textContent = "Network error loading chain.";
      msg.className = "hint err";
    }
  });
}

function renderOcStrikes() {
  const win = Number($("oc-window").value) || 0;
  let rows = ocFullStrikes;
  if (win > 0 && ocFullStrikes.length) {
    const atmIdx = ocFullStrikes.findIndex((s) => s.atm);
    const center = atmIdx >= 0 ? atmIdx : Math.floor(rows.length / 2);
    rows = ocFullStrikes.slice(Math.max(0, center - win),
                               center + win + 1);
  }
  const side = (x) => x ? [
    fmtVol(x.oi), fmtNum(x.oi_change), fmtVol(x.volume),
    x.iv != null ? fmt(x.iv) : "—",
    x.ltp != null ? fmt(x.ltp) : "—",
    (x.close != null && x.ltp != null) ? fmt(x.ltp - x.close) : "—",
  ].map((v) => `<td>${v}</td>`).join("") : "<td>—</td>".repeat(6);
  $("oc-body").innerHTML = rows.map((s) => {
    const rowCls = s.atm ? ' class="bg-accent-dim"' : "";
    return `<tr${rowCls}>` + side(s.call) +
      `<td><b>${fmt(s.strike)}</b></td>` + side(s.put) + "</tr>";
  }).join("");
}
