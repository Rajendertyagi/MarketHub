/**
 * MarketHub WebUI — Market Scanners workspace (Phase C / D).
 *
 * Talks to the generic scanner engine on the backend. When "Ensure live
 * coverage" is checked it reuses the analytics-universe coverage owner so the
 * selected universe's cash equities are subscribed before scanning — the same
 * mechanism Breadth / Heatmap / Market Map use. No broker-socket logic here.
 */

import { apiGet } from "./api.js";
import { ensureAnalyticsCoverage } from "./analytics.js";

let _inited = false;
let _els = {};

function $(id) {
  return document.getElementById(id);
}

export function initScannerUI() {
  if (_inited) return;
  _inited = true;
  _els = {
    name: $("scanner-name"),
    universe: $("scanner-universe"),
    limit: $("scanner-limit"),
    ensure: $("scanner-ensure-coverage"),
    run: $("scanner-run"),
    body: $("scanner-body"),
    title: $("scanner-title"),
    message: $("scanner-message"),
    freshness: $("scanner-freshness"),
  };
  _els.run.addEventListener("click", () => runScan());
  _els.universe.addEventListener("change", () => {
    if (_els.ensure.checked) runScan();
  });
  loadScannerList();
}

async function loadScannerList() {
  try {
    const data = await apiGet("/api/market/scanners");
    const scanners = (data && data.scanners) || [];
    _els.name.innerHTML = scanners
      .map((s) => `<option value="${s.name}">${s.title}</option>`)
      .join("");
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
  try {
    const res = await apiGet(
      `/api/market/scanner/${encodeURIComponent(name)}` +
        `?universe=${encodeURIComponent(universe)}&limit=${encodeURIComponent(limit)}`
    );
    render(res);
    _els.message.textContent = "";
  } catch (e) {
    _els.message.textContent =
      "Scan failed: " + (e && e.message ? e.message : "unknown error");
  }
}

function render(res) {
  if (!res) return;
  _els.title.textContent =
    (res.scanner || "Scanner") + " · " + (res.universe || "");
  _els.freshness.textContent = res.as_of
    ? "as of " + res.as_of
    : `no live quotes (eligible ${res.eligible ?? 0}, quoted ${res.quoted ?? 0})`;
  const rows = res.rows || [];
  if (!rows.length) {
    _els.body.innerHTML =
      `<tr><td colspan="8" class="empty-row">` +
      `No rows (eligible ${res.eligible ?? 0}, quoted ${res.quoted ?? 0}).</td></tr>`;
    return;
  }
  _els.body.innerHTML = rows
    .map((r, i) => {
      const cls =
        r.change_percent > 0 ? "pos" : r.change_percent < 0 ? "neg" : "";
      return `<tr>
        <td>${i + 1}</td>
        <td>${r.symbol}</td>
        <td>${r.sector || "-"}</td>
        <td>${r.ltp != null ? r.ltp : "-"}</td>
        <td class="${cls}">${r.change_percent != null ? r.change_percent.toFixed(2) : "-"}</td>
        <td>${r.volume != null ? r.volume : "-"}</td>
        <td><span class="chip chip-${r.status === "unavailable" ? "off" : "on"}">${r.status}</span></td>
        <td>${r.freshness || "-"}</td>
      </tr>`;
    })
    .join("");
}
