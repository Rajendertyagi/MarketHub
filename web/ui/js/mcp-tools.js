/**
 * MarketHub WebUI — MCP tools/help surface (UI only).
 *
 * Renders the canonical tool registry grouped by category. This module
 * never touches MCP tool handlers, the registry architecture, tool
 * count, or contract versions. Loading is idempotent fetch+render.
 */

const _MCP_CAT_COLORS = {
  "Market": "bg-pos", "Market Alerts": "bg-info",
  "Alerts": "bg-warning", "Condition Alerts": "bg-accent",
  "Compute": "bg-info", "Pricing": "bg-info",
  "Analytics": "bg-warning", "Events": "bg-pos",
  "Consumer": "bg-surface-2", "System": "bg-surface-2",
  "Other": "bg-surface-2",
};
function _mcpCatBadge(cat) {
  const c = _MCP_CAT_COLORS[cat] || "bg-surface-2";
  return `<span class="badge ${c} text-inverse">${cat}</span>`;
}

async function _loadMCPTools() {
  const loadEl = document.getElementById("mcp-tools-loading");
  const emptyEl = document.getElementById("mcp-tools-empty");
  const errEl = document.getElementById("mcp-tools-error");
  const tblEl = document.getElementById("mcp-tools-table");
  const bodyEl = document.getElementById("mcp-tools-body");
  if (!loadEl || !emptyEl || !errEl || !tblEl || !bodyEl) return;
  loadEl.classList.remove("hidden"); emptyEl.classList.add("hidden");
  errEl.classList.add("hidden"); tblEl.classList.add("hidden");
  try {
    const res = await fetch("/api/mcp/tools");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const list = data.tools || [];
    if (!list.length) { loadEl.classList.add("hidden"); emptyEl.classList.remove("hidden"); return; }
    // Group by category from API
    const categories = {};
    list.forEach(t => {
      const cat = t.category || "Other";
      if (!categories[cat]) categories[cat] = [];
      categories[cat].push(t);
    });
    const catOrder = ["Market", "Alerts", "Condition Alerts", "Market Alerts",
      "Compute", "Pricing", "Analytics", "Events", "Consumer", "System", "Other"];
    let html = "";
    catOrder.forEach(cat => {
      const tools = categories[cat];
      if (!tools || !tools.length) return;
      tools.forEach(t => {
        const params = t.input_schema && t.input_schema.properties
          ? Object.keys(t.input_schema.properties).join(", ")
          : "—";
        const required = t.input_schema && t.input_schema.required
          ? t.input_schema.required.join(", ")
          : "";
        const desc = (t.description || "—").split("\n")[0].trim();
        html += `<tr>
          <td><code>${t.name}</code></td>
          <td>${_mcpCatBadge(t.category)}</td>
          <td class="max-col-320 text-sm" title="${(t.description||'').replace(/"/g,'&quot;')}">${desc}</td>
          <td class="text-xs text-muted">${params}${required ? ' <span class="text-warning" title="required">('+required+')</span>' : ''}</td>
        </tr>`;
      });
    });
    bodyEl.innerHTML = html;
    loadEl.classList.add("hidden"); tblEl.classList.remove("hidden");
  } catch (e) {
    loadEl.classList.add("hidden"); errEl.textContent = e.message; errEl.classList.remove("hidden");
  }
}

/** View-enter hook (router): reload the registry table. Idempotent. */
export function openMCPTools() {
  _loadMCPTools();
}

export function initMCPTools() {
  const btn = document.getElementById("mcp-tools-refresh");
  if (btn) btn.addEventListener("click", _loadMCPTools);
}
