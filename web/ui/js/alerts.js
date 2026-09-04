/**
 * MarketHub WebUI — price alerts (CRUD, history, push).
 *
 * Owns the Alerts view: alert CRUD/rearm/enable, trigger-history panel,
 * and the alert-push EventSource (/events/stream). The push stream is a
 * guarded singleton — repeated initialization never duplicates it — and
 * the 15s refresh timers are created exactly once.
 */

import { $, escDash } from "./utils.js";
import { pollSources } from "./market-sources.js";

let alertPushSource = null;              // /events/stream (alert push)
let _alertsInitDone = false;

export function initAlerts() {
  if (_alertsInitDone) return;
  _alertsInitDone = true;
  $("alert-create").addEventListener("click", async () => {
    const token = $("alert-token").value.trim();
    const symbol = $("alert-symbol").value.trim() || token;
    const field = $("alert-field").value;
    const operator = $("alert-operator").value;
    const threshold = parseFloat($("alert-threshold").value);
    if (!token || isNaN(threshold)) return;
    await fetch("/api/alerts", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ exchange: "NSE",
        instrument_token: token, tradingsymbol: symbol,
        field, operator, threshold }) });
    $("alert-threshold").value = "";
    loadAlerts();
  });
  $("alerts-table").addEventListener("click", async (e) => {
    const row = e.target.closest("tr[data-alert-id]");
    if (!row) return;
    const id = row.dataset.alertId;
    if (e.target.classList.contains("alert-rearm")) {
      await fetch(`/api/alerts/${id}/rearm`, { method: "POST" });
    } else if (e.target.classList.contains("alert-toggle")) {
      await fetch(`/api/alerts/${id}/enabled`, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: e.target.dataset.enabled !== "true" }) });
    } else if (e.target.classList.contains("alert-delete")) {
      await fetch(`/api/alerts/${id}`, { method: "DELETE" });
    } else { return; }
    loadAlerts();
  });
  loadAlerts();
  setInterval(loadAlerts, 15000);
  const clearBtn = $("alert-history-clear");
  if (clearBtn) {
    clearBtn.addEventListener("click", async () => {
      if (!confirm("Clear all alert trigger history? This cannot be undone."))
        return;
      await fetch("/api/alerts/history", { method: "DELETE" });
      loadAlertHistory();
    });
  }
  loadAlertHistory();
  setInterval(loadAlertHistory, 15000);
}

async function loadAlertHistory() {
  const body = $("alert-history-body");
  if (!body) return;
  try {
    const res = await fetch("/api/alerts/history?limit=50");
    const data = await res.json();
    const rows = data.history || [];
    if (!rows.length) {
      body.innerHTML =
        '<tr><td colspan="6" class="empty-row">No trigger history yet.</td></tr>';
      return;
    }
    body.innerHTML = rows.map((h) => {
      const cond = `${h.field || ""} ${h.operator === "gt" ? ">" :
        h.operator === "lt" ? "<" : (h.operator || "").replace("crosses_", "crosses ")} ${h.threshold ?? ""}`;
      const t = (h.triggered_at || "").replace("T", " ").slice(0, 19);
      const prov = h.provider ? `<span class="chip">${h.provider}</span>` : "—";
      return `<tr>` +
        `<td>${t}</td>` +
        `<td>${h.tradingsymbol || ""}</td>` +
        `<td>${cond}</td>` +
        `<td>${h.observed_value ?? "—"}</td>` +
        `<td>${prov}</td>` +
        `<td>${h.exchange || ""}</td></tr>`;
    }).join("");
  } catch { /* silent */ }
}

async function loadAlerts() {
  try {
    const res = await fetch("/api/alerts");
    const data = await res.json();
    const body = $("alerts-body");
    const alerts = data.alerts || [];
    if (!alerts.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty-row">No alerts configured.</td></tr>';
    } else {
      body.innerHTML = alerts.map((a) => {
        const cond = `${a.field} ${a.operator === "gt" ? ">" : "<"} ${a.threshold}`;
        const stateCls = a.state === "triggered" ? "chip chip-off" :
          (a.enabled ? "chip chip-on" : "chip");
        return `<tr data-alert-id="${a.id}">` +
          `<td>${escDash(a.tradingsymbol)}</td><td>${cond}</td>` +
          `<td><span class="${stateCls}">${a.state}</span></td>` +
          `<td><button class="btn alert-toggle" data-enabled="${a.enabled ? "true" : "false"}" style="padding:2px 8px;font-size:11px">${a.enabled ? "On" : "Off"}</button></td>` +
          `<td><button class="btn alert-rearm" style="padding:2px 8px;font-size:11px">Re-arm</button> ` +
          `<button class="btn alert-delete" style="padding:2px 8px;font-size:11px;border-color:var(--red);color:var(--red)">✕</button></td></tr>`;
      }).join("");
    }
    const notes = data.notifications || [];
    $("alert-notifications").innerHTML = notes.length
      ? '<div class="panel"><div class="panel-header"><h2>Triggered</h2></div>' +
        notes.map((n) => `<div class="hint err">${escDash(n.tradingsymbol)}: ${n.field} ${n.operator === "gt" ? ">" : "<"} ${n.threshold} (now ${n.value})</div>`).join("") +
        "</div>"
      : "";
  } catch { /* silent */ }
}

export function initAlertPush() {
  if (alertPushSource) return;   // never duplicate
  try {
    alertPushSource = new EventSource("/events/stream");
    alertPushSource.onmessage = (e) => {
      let envelope;
      try { envelope = JSON.parse(e.data); } catch { return; }
      const type = typeof envelope === "string"
        ? JSON.parse(envelope).type : envelope.type;
      if (type === "source.state_changed") {
        // A source changed state (start/stop/reconnect/auth) — refresh the
        // status chips and detail panel immediately (WP22).
        pollSources();
        return;
      }
      if (type !== "alert.triggered") return;
      const d = typeof envelope.data === "string"
        ? JSON.parse(envelope.data) : envelope.data;
      pushAlertNotification(d);
    };
    alertPushSource.onerror = () => { /* EventSource auto-reconnects */ };
  } catch { /* silent */ }
}

function pushAlertNotification(d) {
  const notes = $("alert-notifications");
  if (!notes) return;
  const time = new Date().toLocaleTimeString();
  const cond = `${d.field} ${d.operator === "gt" ? ">" :
    d.operator === "lt" ? "<" : d.operator.replace("crosses_", "crosses ")} ${d.threshold}`;
  const div = document.createElement("div");
  div.className = "hint err";
  div.textContent =
    `${time} — ${escDash(d.tradingsymbol)}: ${cond} (now ${d.observed_value})`;
  notes.prepend(div);
  while (notes.children.length > 20) notes.removeChild(notes.lastChild);
}
