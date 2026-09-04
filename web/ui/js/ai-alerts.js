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
  const map = { true: "warning", false: "neutral", unknown: "neutral" };
  const labels = { true: "Triggered", false: "Normal", unknown: "Unknown" };
  const kind = map[state] || "neutral";
  const l = labels[state] || state;
  return `<span class="ui-badge ui-badge-${kind}">${l}</span>`;
}

function _aiDeliveryBadge(state) {
  const map = {
    acknowledged: "success",
    pending: "warning",
    persisted: "info",
  };
  const kind = map[state] || "info";
  const labels = { acknowledged: "Acknowledged", pending: "Pending", persisted: "Persisted" };
  return `<span class="ui-badge ui-badge-${kind}">${labels[state] || state}</span>`;
}

function _aiEnabledBadge(enabled) {
  return enabled
    ? '<span class="ui-badge ui-badge-success">ON</span>'
    : '<span class="ui-badge ui-badge-neutral">OFF</span>';
}

function _aiModeBadge(mode) {
  const kind = mode === "repeat" ? "info" : "neutral";
  return `<span class="ui-badge ui-badge-${kind}">${mode}</span>`;
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
  loadEl.classList.remove("hidden"); emptyEl.classList.add("hidden");
  errEl.classList.add("hidden"); cardsEl.classList.add("hidden");
  try {
    const data = await _aiFetchJSON("/api/ai-alerts/consumers");
    const list = data.consumers || [];
    if (!list.length) { loadEl.classList.add("hidden"); emptyEl.classList.remove("hidden"); return; }
    cardsEl.innerHTML = list.map(c => `
       <div class="ai-consumer-card">
        <div class="ai-consumer-id">${c.consumer_id}</div>
        <div class="ai-consumer-meta">
          <div>Pending: <strong class="${c.pending_count > 0 ? 'ai-consumer-pending' : ''}">${c.pending_count}</strong></div>
          <div>Unacked: <strong>${c.unacknowledged_count}</strong></div>
          <div>Last trigger: ${c.last_triggered ? _aiTimeAgo(c.last_triggered.trigger_time) : '—'}</div>
          <div>Checkpoint: ${c.last_checkpoint ? '#' + c.last_checkpoint.last_sequence : '—'}</div>
        </div>
      </div>
    `).join("");
    loadEl.classList.add("hidden"); cardsEl.classList.remove("hidden");
  } catch (e) {
    loadEl.classList.add("hidden"); errEl.textContent = e.message; errEl.classList.remove("hidden");
  }
}

async function _loadAIAlerts() {
  const loadEl = document.getElementById("ai-alerts-loading");
  const emptyEl = document.getElementById("ai-alerts-empty");
  const errEl = document.getElementById("ai-alerts-error");
  const tblEl = document.getElementById("ai-alerts-table");
  const bodyEl = document.getElementById("ai-alerts-body");
  loadEl.classList.remove("hidden"); emptyEl.classList.add("hidden");
  errEl.classList.add("hidden"); tblEl.classList.add("hidden");
  try {
    const data = await _aiFetchJSON("/api/ai-alerts");
    const list = data.alerts || [];
    if (!list.length) { loadEl.classList.add("hidden"); emptyEl.classList.remove("hidden"); return; }
    bodyEl.innerHTML = list.map(a => `<tr>
      <td class="mono" title="${a.alert_id}">${_aiShortId(a.alert_id)}</td>
      <td>${a.consumer_id}</td>
      <td>${a.instrument || '—'}</td>
       <td class="ai-alert-condition" title="${a.condition_summary}">${a.condition_summary}</td>
       <td>${_aiModeBadge(a.trigger_mode)}</td>
       <td>${_aiStateBadge(a.current_state)}</td>
       <td>${_aiEnabledBadge(a.enabled)}</td>
       <td class="ai-alert-count">${a.trigger_count}</td>
      <td>${_aiTimeAgo(a.last_triggered_at)}</td>
      <td>${_aiTimeAgo(a.created_at)}</td>
    </tr>`).join("");
    loadEl.classList.add("hidden"); tblEl.classList.remove("hidden");
  } catch (e) {
    loadEl.classList.add("hidden"); errEl.textContent = e.message; errEl.classList.remove("hidden");
  }
}

async function _loadAIEvents() {
  const loadEl = document.getElementById("ai-events-loading");
  const emptyEl = document.getElementById("ai-events-empty");
  const errEl = document.getElementById("ai-events-error");
  const tblEl = document.getElementById("ai-events-table");
  const bodyEl = document.getElementById("ai-events-body");
  loadEl.classList.remove("hidden"); emptyEl.classList.add("hidden");
  errEl.classList.add("hidden"); tblEl.classList.add("hidden");
  try {
    const data = await _aiFetchJSON("/api/ai-alerts/events?limit=200");
    const list = data.events || [];
    if (!list.length) { loadEl.classList.add("hidden"); emptyEl.classList.remove("hidden"); return; }
    bodyEl.innerHTML = list.map(e => `<tr>
      <td class="mono" title="${e.event_id}">${_aiShortId(e.event_id)}</td>
      <td class="mono" title="${e.alert_id}">${_aiShortId(e.alert_id)}</td>
      <td>${e.consumer_id}</td>
      <td>${e.instrument || '—'}</td>
       <td class="ai-event-condition" title="${e.condition_summary}">${e.condition_summary}</td>
      <td>${_aiDeliveryBadge(e.delivery_state)}</td>
      <td>${_aiTimeAgo(e.trigger_time)}</td>
      <td>${e.acknowledged_at ? _aiTimeAgo(e.acknowledged_at) : '—'}</td>
    </tr>`).join("");
    loadEl.classList.add("hidden"); tblEl.classList.remove("hidden");
  } catch (e) {
    loadEl.classList.add("hidden"); errEl.textContent = e.message; errEl.classList.remove("hidden");
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
