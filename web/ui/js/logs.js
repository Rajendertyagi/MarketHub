/**
 * MarketHub WebUI — Logs page (live SSE + filters).
 *
 * Owns its EventSource (created once, guarded), pause/resume,
 * auto-follow, clear-view, and Level/Component/Search filtering for both
 * history loads and live rows.
 */

import { $, fmtLogTs } from "./utils.js";

const LOGS_MAX_BROWSER_ROWS = 500;
let logsEventSource = null;
let logsPaused = false;
let logsAutoFollow = true;

function _connectLogsSSE() {
  if (logsEventSource) return;
  const es = new EventSource("/api/logs/stream");
  logsEventSource = es;
  const statusEl = $("logs-conn-status");

  es.onopen = () => {
    if (statusEl) {
      statusEl.textContent = "● Connected";
      statusEl.className = "logs-conn-on";
    }
  };
  es.onerror = () => {
    if (statusEl) {
      statusEl.textContent = "● Reconnecting";
      statusEl.className = "logs-conn-off";
    }
  };
  es.onmessage = (e) => {
    if (logsPaused) return;
    try {
      const record = JSON.parse(e.data);
      // Apply the same active filters as the history view so live
      // rows never bypass Level / Component / Search.
      if (!_logRecordPassesFilters(record)) return;
      _appendLogRow(record);
    } catch { /* malformed — skip */ }
  };
}

function _logRecordPassesFilters(record) {
  const level = ($("logs-level")?.value || "").trim().toUpperCase();
  if (level && String(record.level || "").toUpperCase() !== level) return false;
  const comp = ($("logs-component")?.value || "").trim().toLowerCase();
  if (comp && !String(record.logger || "").toLowerCase().includes(comp)) return false;
  const search = ($("logs-search")?.value || "").trim().toLowerCase();
  if (search && !String(record.message || "").toLowerCase().includes(search)) return false;
  return true;
}

function _appendLogRow(record) {
  const tbody = $("logs-tbody");
  if (!tbody) return;

  const tr = document.createElement("tr");
  tr.className = "logs-row logs-level-" + (record.level || "").toLowerCase();

  const tdTs = document.createElement("td");
  tdTs.className = "mono logs-ts";
  tdTs.textContent = fmtLogTs(record.ts);
  tr.appendChild(tdTs);

  const tdLevel = document.createElement("td");
  tdLevel.className = "logs-level-cell logs-lvl-" + (record.level || "").toLowerCase();
  tdLevel.textContent = record.level || "—";
  tr.appendChild(tdLevel);

  const tdComp = document.createElement("td");
  tdComp.className = "logs-component";
  tdComp.textContent = record.logger || "—";
  tr.appendChild(tdComp);

  const tdMsg = document.createElement("td");
  tdMsg.className = "logs-message";
  tdMsg.textContent = record.message || "";
  if (record.exception) {
    const exSpan = document.createElement("span");
    exSpan.className = "logs-exception";
    exSpan.textContent = " [" + record.exception.substring(0, 200) + "]";
    tdMsg.appendChild(exSpan);
  }
  tr.appendChild(tdMsg);

  tbody.appendChild(tr);

  // Trim excess rows
  while (tbody.children.length > LOGS_MAX_BROWSER_ROWS) {
    tbody.removeChild(tbody.firstChild);
  }

  // Update count
  const countEl = $("logs-count");
  if (countEl) countEl.textContent = tbody.children.length + " records";

  // Auto-follow
  if (logsAutoFollow) {
    const container = $("logs-container");
    if (container) container.scrollTop = container.scrollHeight;
  }
}

async function _loadLogs() {
  try {
    const level = $("logs-level")?.value || "";
    const component = $("logs-component")?.value || "";
    const search = $("logs-search")?.value || "";
    const params = new URLSearchParams();
    if (level) params.set("level", level);
    if (component) params.set("logger", component);
    if (search) params.set("search", search);
    params.set("limit", "300");

    const resp = await fetch("/api/logs?" + params.toString());
    if (!resp.ok) return;
    const data = await resp.json();
    const tbody = $("logs-tbody");
    if (!tbody) return;
    tbody.innerHTML = "";
    (data.records || []).reverse().forEach(r => _appendLogRow(r));
  } catch { /* network error — ignore */ }
}

function _toggleLogsPause() {
  logsPaused = !logsPaused;
  const btn = $("logs-pause");
  if (btn) btn.textContent = logsPaused ? "Resume" : "Pause";
}

function _clearLogsView() {
  const tbody = $("logs-tbody");
  if (tbody) tbody.innerHTML = "";
  const countEl = $("logs-count");
  if (countEl) countEl.textContent = "0 records";
}

/**
 * View-enter hook (registered once via the router): connect the stream
 * (guarded: exactly one EventSource) and load history.
 */
export function openLogs() {
  _connectLogsSSE();
  _loadLogs();
}

export function initLogsUI() {
  const applyBtn = $("logs-apply");
  if (applyBtn) applyBtn.addEventListener("click", _loadLogs);
  const pauseBtn = $("logs-pause");
  if (pauseBtn) pauseBtn.addEventListener("click", _toggleLogsPause);
  const clearBtn = $("logs-clear");
  if (clearBtn) clearBtn.addEventListener("click", _clearLogsView);
  const autoFollowCb = $("logs-autofollow");
  if (autoFollowCb) autoFollowCb.addEventListener("change", (e) => {
    logsAutoFollow = e.target.checked;
  });
  // Apply filter on Enter key
  ["logs-level", "logs-component", "logs-search"].forEach(id => {
    const el = $(id);
    if (el) el.addEventListener("keydown", (e) => {
      if (e.key === "Enter") _loadLogs();
    });
  });
}
