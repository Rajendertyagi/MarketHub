/**
 * MarketHub WebUI — Settings → AI / MCP (AI provider form).
 *
 * Owns the OpenAI-compatible provider form. Security behavior preserved:
 * the key travels only in a POST JSON body, the field is cleared after a
 * successful save, nothing touches localStorage or the URL. The MCP tool
 * registry reference stays on its own operational page (linked, not moved).
 */

export function initAIMCPSettings() {
  const saveBtn = document.getElementById("ai-save");
  if (!saveBtn) return;
  const msg = document.getElementById("ai-message");
  (async () => {
    try {
      const st = await (await fetch("/api/chat/status")).json();
      if (st.endpoint) {
        document.getElementById("ai-endpoint").value = st.endpoint;
      }
      if (st.model) {
        document.getElementById("ai-model").value = st.model;
      }
    } catch { /* optional */ }
  })();

  saveBtn.addEventListener("click", async () => {
    msg.textContent = "";
    msg.className = "hint";
    const endpoint =
      document.getElementById("ai-endpoint").value.trim();
    const model =
      document.getElementById("ai-model").value.trim();
    const key = document.getElementById("ai-key").value.trim();
    if (!endpoint || !model || !key) {
      msg.textContent = "Endpoint, model and API key are required.";
      msg.className = "hint err";
      return;
    }
    saveBtn.disabled = true;
    try {
      const res = await fetch("/api/chat/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ endpoint, model, api_key: key }),
      });
      const d = await res.json();
      if (res.ok && d.status === "saved") {
        msg.textContent = "AI provider saved. Chat is ready.";
        msg.className = "hint ok";
        document.getElementById("ai-key").value = "";
      } else {
        msg.textContent = d.error || "Save failed.";
        msg.className = "hint err";
      }
    } catch {
      msg.textContent = "Network error saving AI settings.";
      msg.className = "hint err";
    } finally { saveBtn.disabled = false; }
  });
}
