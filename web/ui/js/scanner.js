/**
 * MarketHub WebUI — Market Scanners workspace.
 *
 * Talks to the generic scanner engine on the backend. When "Ensure live
 * coverage" is checked it reuses the analytics-universe coverage owner so the
 * selected universe's cash equities are subscribed before scanning — the same
 * mechanism Breadth / Heatmap / Market Map use. No broker-socket logic here.
 *
 * Derivative scanners (Futures OI, Option IV) reuse the SAME backend engine and
 * API; this module only renders their contextual controls and result columns.
 * Canonical IV arrives as a fraction (0.1758); only the UI formats it as a
 * percent (17.58%) — the underlying value is never mutated.
 */

import { apiGet } from "./api.js";
import { ensureAnalyticsCoverage } from "./analytics.js";
import { switchView } from "./router.js";
import { openStock } from "./fno.js";
import { openChart } from "./charts.js";

let _inited = false;
let _els = {};
let _scanners = {};   // name -> { contract_kind, extra, title }

function $(id) {
  return document.getElementById(id);
}

// Column layout per instrument class. `fmt` controls cell rendering.
const COLUMNS = {
  equity: [
    { key: "symbol", label: "Symbol" },
    { key: "sector", label: "Sector" },
    { key: "ltp", label: "LTP", num: true },
    { key: "change_percent", label: "Chg%", pct: true },
    { key: "volume", label: "Volume", num: true },
    { key: "status", label: "Status" },
    { key: "freshness", label: "Updated" },
  ],
  future: [
    { key: "symbol", label: "Symbol" },
    { key: "contract", label: "Contract" },
    { key: "expiry", label: "Expiry" },
    { key: "ltp", label: "LTP", num: true },
    { key: "change_percent", label: "Chg%", pct: true },
    { key: "oi", label: "OI", num: true },
    { key: "oi_change", label: "OI Δ", num: true, signed: true },
    { key: "oi_change_percent", label: "OI Δ%", pct: true },
    { key: "status", label: "Status" },
    { key: "freshness", label: "Updated" },
  ],
  option: [
    { key: "symbol", label: "Underlying" },
    { key: "contract", label: "Contract" },
    { key: "expiry", label: "Expiry" },
    { key: "strike", label: "Strike", num: true },
    { key: "option_type", label: "CE/PE" },
    { key: "ltp", label: "LTP", num: true },
    { key: "iv", label: "IV%", iv: true },
    { key: "oi", label: "OI", num: true },
    { key: "status", label: "Status" },
    { key: "freshness", label: "Updated" },
  ],
};

export function initScannerUI() {
  if (_inited) return;
  _inited = true;
  _els = {
    name: $("scanner-name"),
    universe: $("scanner-universe"),
    limit: $("scanner-limit"),
    ensure: $("scanner-ensure-coverage"),
    ctx: $("scanner-ctx"),
    expiry: $("scanner-expiry"),
    atm: $("scanner-atm"),
    otype: $("scanner-otype"),
    run: $("scanner-run"),
    head: $("scanner-head"),
    body: $("scanner-body"),
    title: $("scanner-title"),
    message: $("scanner-message"),
    freshness: $("scanner-freshness"),
  };
  _els.run.addEventListener("click", () => runScan());
  _els.universe.addEventListener("change", () => {
    if (_els.ensure.checked) runScan();
  });
  _els.name.addEventListener("change", syncControls);
  _els.body.addEventListener("click", onRowClick);
  loadScannerList();
}

function currentKind() {
  const s = _scanners[_els.name.value];
  return (s && s.contract_kind) || null;
}

// Show derivative-only controls only when the selected scanner needs them.
function syncControls() {
  const isOption = currentKind() === "option";
  _els.ctx.classList.toggle("hidden", !isOption);
  renderHeader();
}

async function loadScannerList() {
  try {
    const data = await apiGet("/api/market/scanners");
    const scanners = (data && data.scanners) || [];
    _scanners = {};
    _els.name.innerHTML = scanners
      .map((s) => {
        _scanners[s.name] = s;
        return `<option value="${s.name}">${s.title}</option>`;
      })
      .join("");
    syncControls();
  } catch {
    _els.message.textContent = "Could not load scanner list.";
  }
}

export async function openScanner() {
  initScannerUI();
  if (_els.ensure.checked && _els.universe.value) {
    await ensureAnalyticsCoverage(_els.universe.value);
  }
  await runScan();
}

async function runScan() {
  const name = _els.name.value;
  const universe = _els.universe.value;
  const limit = _els.limit.value;
  if (!name) return;
  _els.message.textContent = "Scanning…";
  _els.freshness.textContent = "";
  const params = new URLSearchParams({ universe, limit });
  if (currentKind() === "option") {
    if (_els.expiry.value.trim()) params.set("expiry", _els.expiry.value.trim());
    params.set("atm_range", _els.atm.value);
    params.set("option_type", _els.otype.value);
  }
  try {
    const res = await apiGet(
      `/api/market/scanner/${encodeURIComponent(name)}?${params.toString()}`);
    render(res);
    _els.message.textContent = "";
  } catch (e) {
    _els.message.textContent =
      "Scan failed: " + (e && e.message ? e.message : "unknown error");
  }
}

function fmtCell(col, row) {
  const v = row[col.key];
  if (col.iv) {
    // Canonical IV fraction -> percent display ONLY. Value never mutated.
    return v != null ? (v * 100).toFixed(2) + "%" : "-";
  }
  if (col.pct) {
    if (v == null) return "-";
    const cls = v > 0 ? "pos" : v < 0 ? "neg" : "";
    return `<span class="${cls}">${v.toFixed(2)}</span>`;
  }
  if (col.signed) {
    if (v == null) return "-";
    const cls = v > 0 ? "pos" : v < 0 ? "neg" : "";
    return `<span class="${cls}">${v}</span>`;
  }
  if (col.num) {
    return v != null ? v : "-";
  }
  if (col.key === "status") {
    return `<span class="chip chip-${v === "unavailable" ? "off" : "on"}">${v}</span>`;
  }
  return v != null ? v : "-";
}

function renderHeader() {
  const cols = COLUMNS[currentKind() || "equity"];
  _els.head.innerHTML =
    "<tr><th>#</th>" +
    cols.map((c) => `<th>${c.label}</th>`).join("") + "</tr>";
}

function render(res) {
  if (!res) return;
  _els.title.textContent =
    (res.scanner || "Scanner") + " · " + (res.universe || "");
  _els.freshness.textContent = res.as_of
    ? "as of " + res.as_of
    : `no live quotes (eligible ${res.eligible ?? 0}, quoted ${res.quoted ?? 0})`;
  const cols = COLUMNS[currentKind() || "equity"];
  renderHeader();
  const rows = res.rows || [];
  if (!rows.length) {
    _els.body.innerHTML =
      `<tr><td class="empty-row" colspan="${cols.length + 1}">` +
      `No rows (eligible ${res.eligible ?? 0}, quoted ${res.quoted ?? 0}).</td></tr>`;
    return;
  }
  _els.body.innerHTML = rows
    .map((r, i) => {
      const cells = cols
        .map((c) => {
          if (c.key === "symbol") {
            // Symbol cell opens the F&O workspace (Phase 1); the row charts it.
            return `<td><span class="link" data-fno>${fmtCell(c, r)}</span></td>`;
          }
          return `<td>${fmtCell(c, r)}</td>`;
        })
        .join("");
      const kind = currentKind() || "equity";
      return `<tr data-symbol="${r.symbol || ""}" data-kind="${kind}" ` +
        `data-contract="${r.contract || ""}" style="cursor:pointer">` +
        `<td>${i + 1}</td>${cells}</tr>`;
    })
    .join("");
}

// Click a result: the symbol cell reuses the F&O workspace for the underlying
// (Phase 1); clicking anywhere else charts the EXACT instrument (Phase 6) —
// for futures/options that is the derivative contract, never the cash equity.
function onRowClick(e) {
  const tr = e.target.closest("tr[data-symbol]");
  if (!tr) return;
  const sym = tr.getAttribute("data-symbol");
  if (!sym) return;
  if (e.target.closest("[data-fno]")) {
    switchView("fno");
    openStock(sym);
    return;
  }
  const kind = tr.getAttribute("data-kind");
  const contract = tr.getAttribute("data-contract");
  const type = kind === "future" ? "FUTURE"
    : kind === "option" ? "OPTION" : "EQUITY";
  const q = kind === "future" || kind === "option" ? contract : sym;
  openChart({ symbol: q, type });
}
