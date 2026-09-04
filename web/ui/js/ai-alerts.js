/**
 * MarketHub WebUI — AI alert observability.
 *
 * Read-only views over durable alert state: consumers, active condition
 * alerts, triggered/durable events with delivery/pending/ACK status.
 * Semantics preserved: pending means durably assigned/materialized and
 * awaiting ACK — no invented states. Loading is idempotent fetch+render;
 * entering the view (nav click, direct hash, back/forward) reloads it.
 */

function _aiStateBadge(state) {
  const colors = {
    unknown: "var(--text-dim)",
    true: "var(--green)",
    false: "var(--red)",
  };
  const labels = { unknown: "Unknown", true: "Triggered", false: "Normal" };
  const c = colors[state] || colors.unknown;
  const l = labels[state] || state;
  return `<span style="color:${c};font-weight:600">${l}</span>`;
}

function _aiDeliveryBadge(state) {
  const map = {
    acknowledged: { bg: "var(--green)", fg: "#000", label: "Acknowledged" },
    pending: { bg: "var(--yellow)", fg: "#000", label: "Pending" },
    persisted: { bg: "var(--accent)", fg: "#000", label: "Persisted" },
  };
  const s = map[state] || map.persisted;
  return `<span style="background:${s.bg};color:${s.fg};padding:1px 6px;border-radius:3px;font-size:11px;font-weight:600">${s.label}</span>`;
}

function _aiEnabledBadge(enabled) {
  return enabled
    ? '<span style="color:var(--green)">ON</span>'
    : '<span style="color:var(--text-dim)">OFF</span>';
}

function _aiModeBadge(mode) {
  const c = mode === "repeat" ? "var(--accent)" : "var(--yellow)";
  return `<span style="color:${c}">${mode}</span>`;
}

function _aiShortId(id) {
  return id ? id.substring(0, 12) : "—";
}

function _aiTimeAgo(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  if (isNaN(d)) return ts;
  const now = Date.now();
  const diff = (now - d.getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

async function _aiFetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function _loadAIConsumers() {
  const loadEl = document.getElementById("ai-consumers-loading");
  const emptyEl = document.getElementById("ai-consumers-empty");
  const errEl = document.getElementById("ai-consumers-error");
  const cardsEl = document.getElementById("ai-consumers-cards");
  loadEl.style.display = ""; emptyEl.style.display = "none";
  errEl.style.display = "none"; cardsEl.style.display = "none";
  try {
    const data = await _aiFetchJSON("/api/ai-alerts/consumers");
    const list = data.consumers || [];
    if (!list.length) { loadEl.style.display = "none"; emptyEl.style.display = ""; return; }
    cardsEl.innerHTML = list.map(c => `
      <div style="display:inline-block;background:var(--bg-panel-alt);border:1px solid var(--border);border-radius:6px;padding:12px 16px;margin:4px 8px 4px 0;min-width:220px">
        <div style="font-weight:600;margin-bottom:6px">${c.consumer_id}</div>
        <div style="font-size:12px;color:var(--text-muted)">
          <div>Pending: <strong style="color:${c.pending_count > 0 ? 'var(--yellow)' : 'var(--green)'}">${c.pending_count}</strong></div>
          <div>Unacked: <strong>${c.unacknowledged_count}</strong></div>
          <div>Last trigger: ${c.last_triggered ? _aiTimeAgo(c.last_triggered.trigger_time) : '—'}</div>
          <div>Checkpoint: ${c.last_checkpoint ? '#' + c.last_checkpoint.last_sequence : '—'}</div>
        </div>
      </div>
    `).join("");
    loadEl.style.display = "none"; cardsEl.style.display = "";
  } catch (e) {
    loadEl.style.display = "none"; errEl.textContent = e.message; errEl.style.display = "";
  }
}

async function _loadAIAlerts() {
  const loadEl = document.getElementById("ai-alerts-loading");
  const emptyEl = document.getElementById("ai-alerts-empty");
  const errEl = document.getElementById("ai-alerts-error");
  const tblEl = document.getElementById("ai-alerts-table");
  const bodyEl = document.getElementById("ai-alerts-body");
  loadEl.style.display = ""; emptyEl.style.display = "none";
  errEl.style.display = "none"; tblEl.style.display = "none";
  try {
    const data = await _aiFetchJSON("/api/ai-alerts");
    const list = data.alerts || [];
    if (!list.length) { loadEl.style.display = "none"; emptyEl.style.display = ""; return; }
    bodyEl.innerHTML = list.map(a => `<tr>
      <td class="mono" title="${a.alert_id}">${_aiShortId(a.alert_id)}</td>
      <td>${a.consumer_id}</td>
      <td>${a.instrument || '—'}</td>
      <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${a.condition_summary}">${a.condition_summary}</td>
      <td>${_aiModeBadge(a.trigger_mode)}</td>
      <td>${_aiStateBadge(a.current_state)}</td>
      <td>${_aiEnabledBadge(a.enabled)}</td>
      <td style="text-align:right">${a.trigger_count}</td>
      <td>${_aiTimeAgo(a.last_triggered_at)}</td>
      <td>${_aiTimeAgo(a.created_at)}</td>
    </tr>`).join("");
    loadEl.style.display = "none"; tblEl.style.display = "";
  } catch (e) {
    loadEl.style.display = "none"; errEl.textContent = e.message; errEl.style.display = "";
  }
}

async function _loadAIEvents() {
  const loadEl = document.getElementById("ai-events-loading");
  const emptyEl = document.getElementById("ai-events-empty");
  const errEl = document.getElementById("ai-events-error");
  const tblEl = document.getElementById("ai-events-table");
  const bodyEl = document.getElementById("ai-events-body");
  loadEl.style.display = ""; emptyEl.style.display = "none";
  errEl.style.display = "none"; tblEl.style.display = "none";
  try {
    const data = await _aiFetchJSON("/api/ai-alerts/events?limit=200");
    const list = data.events || [];
    if (!list.length) { loadEl.style.display = "none"; emptyEl.style.display = ""; return; }
    bodyEl.innerHTML = list.map(e => `<tr>
      <td class="mono" title="${e.event_id}">${_aiShortId(e.event_id)}</td>
      <td class="mono" title="${e.alert_id}">${_aiShortId(e.alert_id)}</td>
      <td>${e.consumer_id}</td>
      <td>${e.instrument || '—'}</td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${e.condition_summary}">${e.condition_summary}</td>
      <td>${_aiDeliveryBadge(e.delivery_state)}</td>
      <td>${_aiTimeAgo(e.trigger_time)}</td>
      <td>${e.acknowledged_at ? _aiTimeAgo(e.acknowledged_at) : '—'}</td>
    </tr>`).join("");
    loadEl.style.display = "none"; tblEl.style.display = "";
  } catch (e) {
    loadEl.style.display = "none"; errEl.textContent = e.message; errEl.style.display = "";
  }
}

/** View-enter hook (router): reload all three panels. Idempotent. */
export function openAIAlerts() {
  _loadAIConsumers(); _loadAIAlerts(); _loadAIEvents();
}

export function initAIAlerts() {
  const refreshBtn = document.getElementById("ai-alerts-refresh");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => {
      _loadAIConsumers(); _loadAIAlerts(); _loadAIEvents();
    });
  }
}
