/**
 * MarketHub WebUI — Test Center (Diagnostics) page.
 *
 * Two modes: Quick Health Check (everyday) and Full Test (comprehensive).
 *
 * Browser-owned diagnostics:
 *   - SSE: a REAL browser EventSource to /api/market/stream (bounded window).
 *   - Parity: REST (backend) vs MCP (backend) vs SSE (browser) for the
 *     selected instrument. Browser SSE state is NEVER sent back to Python.
 *
 * Pattern: follows mcp-tools.js + logs.js conventions.
 */

import { apiGet } from "./api.js";
import { $, esc } from "./utils.js";

let _running = false;
let _lastResults = null;
let _sseEventSource = null;
let _sseEvidence = null;
let _checkDefs = [];

const CANONICAL_SYMBOLS = [
  "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
  "NIFTYNXT50", "INDIAVIX", "SENSEX", "BANKEX",
];

export function initDiagnosticsUI() {
  const runQuick = document.getElementById("diag-run-quick");
  const runFull = document.getElementById("diag-run-full");
  const refreshBtn = document.getElementById("diag-refresh");
  const copyBtn = document.getElementById("diag-copy-report");
  const symbolSel = document.getElementById("diag-symbol");

  if (runQuick) runQuick.addEventListener("click", () => runChecks("quick"));
  if (runFull) runFull.addEventListener("click", () => runChecks("full"));
  if (refreshBtn) refreshBtn.addEventListener("click", () => {
    runChecks(_lastResults ? _lastResults.mode : "quick");
  });
  if (copyBtn) copyBtn.addEventListener("click", copyReport);
  if (symbolSel) symbolSel.addEventListener("change", () => {
    runChecks(_lastResults ? _lastResults.mode : "quick");
  });

  _populateSymbolSelector();
  // Load check definitions once (used only for selector hints)
  apiGet("/api/diagnostics/checks")
    .then(defs => { _checkDefs = defs.checks || []; })
    .catch(() => {});
}

export function openDiagnostics() {
  // Begin the SSE diagnostic immediately when the page is opened.
  _startSSEDiagnostic();
}

function _populateSymbolSelector() {
  const sel = document.getElementById("diag-symbol");
  if (!sel) return;
  // Keep NIFTY default; add the canonical indices.
  const existing = new Set([...sel.options].map(o => o.value));
  for (const s of CANONICAL_SYMBOLS) {
    if (!existing.has(s)) {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      sel.appendChild(opt);
    }
  }
}

function _startSSEDiagnostic(onDone) {
  if (_sseEventSource) {
    try { _sseEventSource.close(); } catch {}
    _sseEventSource = null;
  }
  _sseEvidence = {
    connected: false, reset: false, quote: null, error: null, status: "connecting",
  };
  _updateSSEStatus("connecting");

  let finished = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    if (_sseEventSource) {
      try { _sseEventSource.close(); } catch {}
      _sseEventSource = null;
    }
    if (onDone) onDone();
  };

  let es;
  try {
    es = new EventSource("/api/market/stream");
  } catch (e) {
    _sseEvidence.error = String(e);
    _updateSSEStatus("disconnected");
    if (onDone) onDone();
    return;
  }
  _sseEventSource = es;

  es.onopen = () => {
    _sseEvidence.connected = true;
    if (_sseEvidence.reset) _updateSSEStatus("reset");
  };
  es.addEventListener("reset", () => {
    _sseEvidence.connected = true;
    _sseEvidence.reset = true;
    _updateSSEStatus("reset");
  });
  es.addEventListener("quote", (e) => {
    try {
      const payload = JSON.parse(e.data);
      const d = payload.data || payload;
      _sseEvidence.quote = {
        instrument: d.instrument_key || d.symbol || d.instrument_token,
        value: d.ltp ?? d.last_price ?? d.value,
        ts: d.received_ts || d.timestamp || null,
      };
      _updateSSEStatus("quote-received");
    } catch {}
  });
  es.onerror = () => {
    if (!_sseEvidence.connected && !_sseEvidence.reset) {
      _sseEvidence.error = "connection_error";
    }
    _updateSSEStatus("disconnected");
  };

  // Bounded window: do not hang. Close after 8s regardless.
  setTimeout(finish, 8000);
}

function _updateSSEStatus(state) {
  const el = $("diag-sse-status");
  if (!el) return;
  const labels = {
    "connecting": "SSE: connecting…",
    "connected": "SSE: connected",
    "reset": "SSE: reset observed",
    "quote-received": "SSE: quote received",
    "disconnected": "SSE: disconnected",
    "timeout": "SSE: timeout (no reset)",
  };
  el.textContent = labels[state] || state;
}

function _sseClassification() {
  const s = _sseEvidence;
  if (!s) return { status: "UNAVAILABLE", message: "SSE diagnostic not run" };
  if (s.error && !s.connected && !s.reset) {
    return { status: "FAIL", message: `SSE connection failed: ${s.error}` };
  }
  if (s.quote) {
    return { status: "PASS",
      message: `SSE connected, reset observed, quote captured (${s.quote.instrument}: ${s.quote.value})` };
  }
  if (s.connected || s.reset) {
    return { status: "PARTIAL",
      message: "SSE connected and reset observed, but no quote in window (market closed/quiet)" };
  }
  return { status: "FAIL", message: "SSE did not connect / no reset event" };
}

function _computeParity() {
  if (!_lastResults) return null;
  const results = _lastResults.results || [];
  const sym = (_lastResults.symbol || "NIFTY").toUpperCase();

  const restCandidates = results.filter(
    r => r.id && r.id.endsWith("_quote") && r.layer === "REST");
  const rest = restCandidates.find(
    r => r.id.toLowerCase().startsWith(sym.toLowerCase())) || restCandidates[0];
  const mcp = results.find(r => r.id === "mcp_quote");
  const sse = _sseEvidence;

  const restHas = !!rest && rest.status === "PASS";
  const mcpHas = !!mcp && mcp.status === "PASS";
  const sseHas = !!(sse && sse.quote);

  const num = (v) => (v == null ? null : parseFloat(v));
  const restLtp = num(rest?.data?.ltp) ??
    num(rest?.message?.match(/LTP:\s*([\d.]+)/)?.[1]);
  const mcpLtp = num(mcp?.data?.quote?.ltp) ??
    num(mcp?.message?.match(/LTP:\s*([\d.]+)/)?.[1]);
  const sseLtp = num(sse?.quote?.value);

  const tol = (a, b) => Math.abs(a - b) < 0.01 * Math.max(1, Math.abs(a));

  let status, message;
  if (restHas && mcpHas) {
    if (restLtp != null && mcpLtp != null && !tol(restLtp, mcpLtp)) {
      status = "FAIL"; message = "REST and MCP quotes disagree";
    } else if (sseHas && sseLtp != null && restLtp != null && !tol(sseLtp, restLtp)) {
      status = "FAIL"; message = "SSE quote materially disagrees with REST/MCP";
    } else {
      status = "PASS"; message = "REST, MCP and SSE agree";
    }
  } else if (restHas && !mcpHas) {
    status = "FAIL"; message = "REST has quote but MCP cannot resolve it";
  } else if (!restHas && !mcpHas) {
    if (sse && sse.error && !sse.connected) {
      status = "FAIL"; message = "SSE connection failed; parity cannot be verified";
    } else if (sseHas) {
      status = "PARTIAL"; message = "REST/MCP no quote but SSE delivered a quote";
    } else {
      status = "PARTIAL";
      message = "REST and MCP agree (no quote); SSE connected but no fresh quote (market closed/quiet)";
    }
  } else {
    status = "PARTIAL"; message = "Incomplete quote evidence for parity";
  }

  return {
    status, message,
    detail: {
      symbol: sym,
      rest: rest ? rest.status : "absent",
      mcp: mcp ? mcp.status : "absent",
      sse: sseHas ? "quote" : "no_quote",
      rest_ltp: restLtp, mcp_ltp: mcpLtp, sse_ltp: sseLtp,
    },
  };
}

async function runChecks(mode) {
  if (_running) return;
  _running = true;

  const statusEl = $("diag-status");
  const summaryEl = $("diag-summary");
  const failuresEl = $("diag-failures");
  const warningsEl = $("diag-warnings");
  const resultsEl = $("diag-results");
  const sseStatusEl = $("diag-sse-status");

  statusEl.textContent = mode === "full"
    ? "Running Full Test…" : "Running Quick Health Check…";
  statusEl.className = "hint";
  summaryEl.innerHTML = "";
  failuresEl.innerHTML = "";
  failuresEl.classList.add("hidden");
  warningsEl.innerHTML = "";
  warningsEl.classList.add("hidden");
  resultsEl.innerHTML = "";
  if (sseStatusEl) sseStatusEl.textContent = "SSE: connecting…";

  // Reset browser-owned diagnostics
  _lastResults = null;
  _sseEvidence = null;

  // Start the real browser SSE diagnostic (bounded). It refreshes the
  // SSE + parity cards when it completes.
  _startSSEDiagnostic(() => _renderSSEAndParity(resultsEl));

  try {
    const sym = (document.getElementById("diag-symbol")?.value || "NIFTY")
      .trim().toUpperCase();
    const res = await apiGet(
      `/api/diagnostics/run?mode=${mode}&symbol=${encodeURIComponent(sym)}`);
    _lastResults = { ...res, mode };

    const s = res.summary || {};
    summaryEl.innerHTML =
      `<span class="ui-badge success">${s.PASS || 0} passed</span> ` +
      `<span class="ui-badge warning">${s.PARTIAL || 0} partial</span> ` +
      `<span class="ui-badge danger">${s.FAIL || 0} failed</span> ` +
      `<span class="ui-badge neutral">${s.UNAVAILABLE || 0} unavailable</span> ` +
      `<span class="ui-badge neutral">${s.SKIPPED || 0} skipped</span>`;

    if (res.failures?.length) {
      failuresEl.innerHTML = "<strong>FAILURES:</strong> " +
        res.failures.map(id => `<code>${esc(id)}</code>`).join(", ");
      failuresEl.classList.remove("hidden");
    }
    if (res.warnings?.length) {
      warningsEl.innerHTML = "<strong>WARNINGS:</strong> " +
        res.warnings.map(id => `<code>${esc(id)}</code>`).join(", ");
      warningsEl.classList.remove("hidden");
    }

    const groups = _groupByCategory(res.results);
    const catOrder = ["SYSTEM", "BROKERS", "MARKET DATA", "TRANSPORT",
                      "OPTIONS", "HISTORY", "NEWS", "MCP", "PARITY"];
    for (const cat of catOrder) {
      const checks = groups[cat];
      if (!checks || !checks.length) continue;
      _renderCategory(resultsEl, cat, checks);
    }
    Object.keys(groups).forEach(cat => {
      if (catOrder.includes(cat)) return;
      _renderCategory(resultsEl, cat, groups[cat]);
    });

    // Render SSE + parity cards (initial pass; refreshed when SSE finishes).
    _renderSSEAndParity(resultsEl);

    statusEl.textContent = `Done in ${res.duration_ms}ms (${mode}).`;
    statusEl.className = "hint ok";
  } catch (e) {
    statusEl.textContent = `Error: ${e.message}`;
    statusEl.className = "hint err";
  } finally {
    _running = false;
  }
}

function _renderSSEAndParity(container) {
  // Remove previous SSE/parity cards to avoid duplicates on refresh.
  ["diag-card-sse", "diag-card-parity"].forEach(id => {
    const old = document.getElementById(id);
    if (old) old.remove();
  });

  const sse = _sseClassification();
  _renderSingleCard(container, "TRANSPORT", "Browser SSE Diagnostic",
    sse.status, sse.message, _sseEvidence, "diag-card-sse");

  const parity = _computeParity();
  if (parity) {
    _renderSingleCard(container, "PARITY", "REST ↔ SSE ↔ MCP Quote Parity",
      parity.status, parity.message, parity.detail, "diag-card-parity");
  }
}

function _renderSingleCard(container, category, name, status, message, data, id) {
  const div = document.createElement("div");
  div.className = "diag-category";
  div.id = id || "";
  div.innerHTML = `<h3>${esc(category)}</h3>`;

  const table = document.createElement("table");
  table.className = "data-table";
  table.innerHTML = `<thead><tr><th>Status</th><th>Name</th><th>Layer</th><th>Message</th></tr></thead>`;
  const tbody = document.createElement("tbody");

  const badgeClass = status === "PASS" ? "success" :
    status === "PARTIAL" ? "warning" :
    status === "FAIL" ? "danger" :
    status === "UNAVAILABLE" ? "neutral" : "neutral";
  const tr = document.createElement("tr");
  tr.className = "diag-card";
  tr.innerHTML =
    `<td><span class="ui-badge ui-badge-${badgeClass}">${esc(status)}</span></td>` +
    `<td><strong>${esc(name)}</strong></td>` +
    `<td class="mono">browser</td>` +
    `<td>${esc(message)}</td>`;

  const detailsDiv = document.createElement("div");
  detailsDiv.className = "diag-details hidden";
  const reportLines = [
    `Status: ${status}`,
    `Message: ${message}`,
  ];
  if (data && Object.keys(data).length > 0) {
    reportLines.push(`Data: ${JSON.stringify(data, null, 2)}`);
  }
  detailsDiv.innerHTML = `<pre class="mono">${esc(reportLines.join("\n"))}</pre>`;
  tr.addEventListener("click", () => detailsDiv.classList.toggle("hidden"));
  tr.appendChild(detailsDiv);
  tbody.appendChild(tr);

  table.appendChild(tbody);
  div.appendChild(table);
  container.appendChild(div);
}

function _renderCategory(container, category, checks) {
  const div = document.createElement("div");
  div.className = "diag-category";
  div.innerHTML = `<h3>${esc(category)}</h3>`;

  const table = document.createElement("table");
  table.className = "data-table";
  table.innerHTML = `<thead><tr><th>Status</th><th>Name</th><th>Layer</th><th>Message</th><th>Time</th></tr></thead>`;
  const tbody = document.createElement("tbody");

  for (const r of checks) {
    const badgeClass = r.status === "PASS" ? "success" :
                      r.status === "PARTIAL" ? "warning" :
                      r.status === "FAIL" ? "danger" :
                      r.status === "UNAVAILABLE" ? "neutral" : "neutral";
    const tr = document.createElement("tr");
    tr.className = "diag-card";
    tr.innerHTML =
      `<td><span class="ui-badge ui-badge-${badgeClass}">${esc(r.status)}</span></td>` +
      `<td><strong>${esc(r.name)}</strong></td>` +
      `<td class="mono">${esc(r.layer)}</td>` +
      `<td>${esc(r.message)}</td>` +
      `<td class="mono">${r.duration_ms}ms</td>`;

    const detailsDiv = document.createElement("div");
    detailsDiv.className = "diag-details hidden";
    const reportLines = [
      `ID: ${r.id}`,
      `Status: ${r.status}`,
      `Message: ${r.message}`,
      `Classification: ${r.classification_reason}`,
      `Duration: ${r.duration_ms}ms`,
    ];
    if (r.data && Object.keys(r.data).length > 0) {
      reportLines.push(`Data: ${JSON.stringify(r.data, null, 2)}`);
    }
    detailsDiv.innerHTML = `<pre class="mono">${esc(reportLines.join("\n"))}</pre>`;
    tr.addEventListener("click", () => detailsDiv.classList.toggle("hidden"));
    tr.appendChild(detailsDiv);
    tbody.appendChild(tr);
  }

  div.appendChild(table);
  container.appendChild(div);
}

function copyReport() {
  if (!_lastResults) return;
  const s = _lastResults.summary || {};
  const sse = _sseClassification();
  const parity = _computeParity();
  const lines = [
    "MARKETHUB TEST CENTER REPORT",
    `Generated: ${_lastResults.run_at || new Date().toISOString()}`,
    `Mode: ${_lastResults.mode}`,
    `Symbol: ${_lastResults.symbol || "NIFTY"}`,
    `Duration: ${_lastResults.duration_ms}ms`,
    "",
    `SUMMARY`,
    `  PASS=${s.PASS || 0} PARTIAL=${s.PARTIAL || 0} FAIL=${s.FAIL || 0} UNAVAILABLE=${s.UNAVAILABLE || 0} SKIPPED=${s.SKIPPED || 0}`,
    "",
  ];

  // Browser SSE diagnostic
  lines.push("BROWSER SSE DIAGNOSTIC");
  lines.push(`  [${sse.status}] ${sse.message}`);
  if (_sseEvidence) {
    lines.push(`  connected=${_sseEvidence.connected} reset=${_sseEvidence.reset} quote=${_sseEvidence.quote ? "captured" : "none"}`);
  }
  lines.push("");

  // Parity
  if (parity) {
    lines.push("REST ↔ SSE ↔ MCP QUOTE PARITY");
    lines.push(`  [${parity.status}] ${parity.message}`);
    const d = parity.detail || {};
    lines.push(`  symbol=${d.symbol} rest=${d.rest} mcp=${d.mcp} sse=${d.sse}`);
    lines.push(`  rest_ltp=${d.rest_ltp} mcp_ltp=${d.mcp_ltp} sse_ltp=${d.sse_ltp}`);
    lines.push("");
  }

  if (_lastResults.failures?.length) {
    lines.push("FAILURES:");
    _lastResults.failures.forEach(id => lines.push(`  - ${id}`));
    lines.push("");
  }
  if (_lastResults.warnings?.length) {
    lines.push("WARNINGS:");
    _lastResults.warnings.forEach(id => lines.push(`  - ${id}`));
    lines.push("");
  }

  lines.push("DETAILS");
  (_lastResults.results || []).forEach(r => {
    lines.push(`[${r.status}] ${r.name} (${r.category}/${r.layer}): ${r.message}`);
  });

  const text = lines.join("\n");
  navigator.clipboard.writeText(text).then(() => {
    const btn = $("diag-copy-report");
    if (btn) {
      const orig = btn.textContent;
      btn.textContent = "Copied!";
      setTimeout(() => { btn.textContent = orig; }, 2000);
    }
  }).catch(() => {
    prompt("Copy this report:", text);
  });
}

function _groupByCategory(results) {
  const groups = {};
  for (const r of results) {
    const cat = r.category || "Other";
    if (!groups[cat]) groups[cat] = [];
    groups[cat].push(r);
  }
  return groups;
}
