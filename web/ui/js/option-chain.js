/**
 * MarketHub WebUI — option chain (F&O).
 *
 * Connects the screen to the canonical option-chain backend
 * (GET /api/options/chain/view), which is catalog-driven and works without a
 * live broker: it always returns the real strike ladder (CE/PE), spot and ATM,
 * and attaches a live `quote` to each contract when a market feed is connected.
 *
 * Selection state is module-local (never implicit globals). Analytics are
 * derived client-side from the returned quotes so they work whenever market
 * data is present and show "—" honestly when it is not.
 */

import { $, fmt, fmtVol, chgClass, esc } from "./utils.js";
import { apiGet } from "./api.js";

let ocUnderlying = null;
let ocExpiry = null;
let ocLoading = false;

export function initOptionChain() {
  const search = $("oc-underlying-search");
  const sel = $("oc-underlying-select");
  const expSel = $("oc-expiry-select");
  const winSel = $("oc-window");
  const msg = $("oc-message");

  let debounce = null;
  search.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(async () => {
      const q = search.value.trim();
      if (!q) return;
      try {
        const d = await apiGet(
          "/api/options/underlyings?q=" + encodeURIComponent(q));
        const list = d.underlyings || [];
        sel.innerHTML = '<option value="">Underlying…</option>' +
          list.map((u) => `<option value="${esc(u)}">${esc(u)}</option>`).join("");
        if (!list.length) setMsg("No matching underlyings in catalog.", true);
      } catch { /* silent — keep prior list */ }
    }, 300);
  });

  sel.addEventListener("change", async () => {
    ocUnderlying = sel.value;
    expSel.innerHTML = '<option value="">Expiry…</option>';
    expSel.disabled = true;
    ocExpiry = null;
    if (!ocUnderlying) { renderEmpty(); return; }
    try {
      const d = await apiGet(
        "/api/options/expiries?underlying=" + encodeURIComponent(ocUnderlying));
      const exps = d.expiries || [];
      expSel.innerHTML = '<option value="">Expiry…</option>' +
        exps.map((e) => `<option value="${esc(e)}">${esc(e)}</option>`).join("");
      expSel.disabled = !exps.length;
      if (exps.length) {
        expSel.value = exps[0];
        ocExpiry = exps[0];
        await loadChain();
      } else {
        setMsg(`No listed option expiries for ${ocUnderlying}.`, true);
        renderEmpty();
      }
    } catch (e) {
      setMsg(e.message || "Failed to load expiries.", true);
      renderEmpty();
    }
  });

  expSel.addEventListener("change", async () => {
    ocExpiry = expSel.value;
    if (ocUnderlying && ocExpiry) await loadChain();
  });

  winSel.addEventListener("change", () => {
    if (ocUnderlying && ocExpiry) loadChain();
  });

  $("oc-load").addEventListener("click", () => {
    if (ocUnderlying && ocExpiry) loadChain();
    else setMsg("Pick an underlying and expiry first.", true);
  });
}

async function loadChain() {
  if (ocLoading || !ocUnderlying || !ocExpiry) return;
  ocLoading = true;
  setMsg("Loading option chain…", false, true);
  const win = Number($("oc-window").value) || 0;
  const url = "/api/options/chain/view?underlying=" +
    encodeURIComponent(ocUnderlying) +
    "&expiry=" + encodeURIComponent(ocExpiry) +
    "&window=" + (win || 250);
  try {
    const d = await apiGet(url);
    if (d.error) { setMsg(d.error, true); renderEmpty(); return; }
    renderChain(d);
    const basis = d.spot_basis && d.spot_basis !== "live"
      ? `Spot is ${d.spot_basis} (no live feed — strike ladder is real catalog data).`
      : "";
    setMsg(basis, false);
  } catch (e) {
    setMsg(e.message || "Failed to load chain.", true);
    renderEmpty();
  } finally {
    ocLoading = false;
  }
}

function renderChain(d) {
  $("oc-spot").textContent = d.spot != null ? fmt(d.spot) : "—";
  $("oc-atm").textContent = d.atm_strike != null ? fmt(d.atm_strike) : "—";
  $("oc-exp-label").textContent = d.expiry || "—";
  const rows = d.rows || [];
  $("oc-strikes").textContent = rows.length
    ? `${rows.length} / ${d.strikes_total_listed != null ? d.strikes_total_listed : rows.length}`
    : "—";

  const side = (leg) => {
    if (!leg) return "<td>—</td>".repeat(7);
    const q = leg.quote || null;
    const oi = q ? q.open_interest : null;
    const oiChg = q ? q.oi_change : null;
    const vol = q ? q.volume : null;
    const iv = q ? q.iv : null;
    const ltp = q ? q.ltp : null;
    const chg = q ? q.change : null;
    const greeks = q ? greeksStr(q) : null;
    const cell = (v, cls) => `<td class="${cls || ""}">${
      v != null ? v : "—"}</td>`;
    return [
      cell(oi != null ? fmtVol(oi) : null),
      cell(oiChg != null ? fmtVol(oiChg) : null, chgClass(oiChg)),
      cell(vol != null ? fmtVol(vol) : null),
      cell(iv != null ? (iv * 100).toFixed(2) + "%" : null),
      cell(ltp != null ? fmt(ltp) : null, chgClass(chg)),
      cell(chg != null ? fmt(chg) : null, chgClass(chg)),
      cell(greeks, "oc-greeks"),
    ].join("");
  };

  if (!rows.length) {
    $("oc-body").innerHTML =
      '<tr><td colspan="15" class="empty-row">No option contracts listed for this expiry.</td></tr>';
  } else {
    $("oc-body").innerHTML = rows.map((r) => {
      const rowCls = r.atm ? ' class="option-chain-atm"' : "";
      return `<tr${rowCls}>` + side(r.call) +
        `<td class="strike-col"><b>${fmt(r.strike)}</b></td>` +
        side(r.put) + "</tr>";
    }).join("");
  }
  renderAnalytics(rows);
}

function greeksStr(q) {
  const parts = [];
  if (q.delta != null) parts.push("D" + q.delta.toFixed(2));
  if (q.gamma != null) parts.push("G" + q.gamma.toFixed(3));
  if (q.theta != null) parts.push("T" + q.theta.toFixed(2));
  if (q.vega != null) parts.push("V" + q.vega.toFixed(2));
  if (q.rho != null) parts.push("R" + q.rho.toFixed(2));
  return parts.length ? parts.join(" ") : null;
}

function renderAnalytics(rows) {
  let callOI = 0, putOI = 0, callDOI = 0, putDOI = 0;
  let hasOI = false, hasDOI = false;
  let atmCallLtp = null, atmPutLtp = null;
  for (const r of rows) {
    if (r.call && r.call.quote) {
      const q = r.call.quote;
      if (q.open_interest != null) { callOI += q.open_interest; hasOI = true; }
      if (q.oi_change != null) { callDOI += q.oi_change; hasDOI = true; }
      if (r.atm && q.ltp != null) atmCallLtp = q.ltp;
    }
    if (r.put && r.put.quote) {
      const q = r.put.quote;
      if (q.open_interest != null) { putOI += q.open_interest; hasOI = true; }
      if (q.oi_change != null) { putDOI += q.oi_change; hasDOI = true; }
      if (r.atm && q.ltp != null) atmPutLtp = q.ltp;
    }
  }
  $("oc-pcr").textContent = (hasOI && callOI > 0)
    ? (putOI / callOI).toFixed(3) : "—";
  $("oc-ce-oi").textContent = hasOI ? fmtVol(callOI) : "—";
  $("oc-pe-oi").textContent = hasOI ? fmtVol(putOI) : "—";
  $("oc-ce-doi").textContent = hasDOI ? fmtVol(callDOI) : "—";
  $("oc-pe-doi").textContent = hasDOI ? fmtVol(putDOI) : "—";
  const straddle = (atmCallLtp != null && atmPutLtp != null)
    ? atmCallLtp + atmPutLtp : null;
  $("oc-straddle").textContent = straddle != null ? fmt(straddle) : "—";
}

function renderEmpty() {
  $("oc-spot").textContent = "—";
  $("oc-atm").textContent = "—";
  $("oc-exp-label").textContent = "—";
  $("oc-strikes").textContent = "—";
  $("oc-pcr").textContent = "—";
  $("oc-ce-oi").textContent = "—";
  $("oc-pe-oi").textContent = "—";
  $("oc-ce-doi").textContent = "—";
  $("oc-pe-doi").textContent = "—";
  $("oc-straddle").textContent = "—";
  $("oc-body").innerHTML =
    '<tr><td colspan="15" class="empty-row">Search an underlying, pick an expiry, then Load Chain.</td></tr>';
}

function setMsg(text, isError, isLoading) {
  const msg = $("oc-message");
  msg.textContent = text || "";
  msg.className = "hint" + (isError ? " err" : isLoading ? " loading" : "");
}
