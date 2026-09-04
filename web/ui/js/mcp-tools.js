/**
 * MarketHub WebUI — MCP tools/help surface (UI only).
 *
 * Renders the canonical tool registry grouped by category. This module
 * never touches MCP tool handlers, the registry architecture, tool
 * count, or contract versions. Loading is idempotent fetch+render.
 */

const _MCP_CAT_COLORS = {
  "Market": "var(--green)", "Market Alerts": "var(--cyan)",
  "Alerts": "var(--yellow)", "Condition Alerts": "var(--accent)",
  "Compute": "var(--magenta)", "Pricing": "var(--cyan)",
  "Analytics": "var(--yellow)", "Events": "var(--green)",
  "Consumer": "var(--text-muted)", "System": "var(--text-muted)",
  "Other": "var(--text-muted)",
};
function _mcpCatBadge(cat) {
  const c = _MCP_CAT_COLORS[cat] || "var(--text-muted)";
  return `<span style="display:inline-block;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:600;background:${c};color:#000">${cat}</span>`;
}

async function _loadMCPTools() {
  const loadEl = document.getElementById("mcp-tools-loading");
  const emptyEl = document.getElementById("mcp-tools-empty");
  const errEl = document.getElementById("mcp-tools-error");
  const tblEl = document.getElementById("mcp-tools-table");
  const bodyEl = document.getElementById("mcp-tools-body");
  if (!loadEl) return;
  loadEl.style.display = ""; emptyEl.style.display = "none";
  errEl.style.display = "none"; tblEl.style.display = "none";
  try {
    const res = await fetch("/api/mcp/tools");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const list = data.tools || [];
    if (!list.length) { loadEl.style.display = "none"; emptyEl.style.display = ""; return; }
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
          <td style="max-width:320px;font-size:12px" title="${(t.description||'').replace(/"/g,'&quot;')}">${desc}</td>
          <td style="font-size:11px;color:var(--text-muted)">${params}${required ? ' <span style="color:var(--yellow)" title="required">('+required+')</span>' : ''}</td>
        </tr>`;
      });
    });
    bodyEl.innerHTML = html;
    loadEl.style.display = "none"; tblEl.style.display = "";
  } catch (e) {
    loadEl.style.display = "none"; errEl.textContent = e.message; errEl.style.display = "";
  }
}

/** View-enter hook (router): reload the registry table. Idempotent. */
export function openMCPTools() {
  _loadMCPTools();
}

export function initMCPTools() {
  const refreshBtn = document.getElementById("mcp-tools-refresh");
  if (refreshBtn) refreshBtn.addEventListener("click", _loadMCPTools);
}
