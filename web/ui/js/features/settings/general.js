/**
 * MarketHub WebUI — Settings → General (application preferences).
 *
 * Owns the public-base-URL form. Theme toggling stays global (app.js);
 * nothing here invents new settings.
 */

import { $ } from "../../utils.js";

export function initGeneralSettings() {
  const saveBtn = $("app-save");
  if (!saveBtn) return;
  const msg = $("app-message");

  async function refresh() {
    try {
      const res = await fetch("/api/settings/app");
      const d = await res.json();
      $("app-base-url").textContent = d.public_base_url || "—";
      $("app-base-url").className =
        "chip " + (d.public_base_url ? "chip-on" : "chip-off");
      $("app-fyers-callback").textContent = d.fyers_callback_url || "—";
      $("app-base-url-input").value = d.public_base_url || "";
    } catch { /* silent */ }
  }

  saveBtn.addEventListener("click", async () => {
    const base = $("app-base-url-input").value.trim();
    msg.textContent = "";
    msg.className = "hint";
    if (!base) {
      msg.textContent = "Public Base URL is required.";
      msg.className = "hint err";
      return;
    }
    saveBtn.disabled = true;
    try {
      const res = await fetch("/api/settings/app", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ public_base_url: base }) });
      const d = await res.json();
      if (res.ok && d.status === "ok") {
        msg.textContent = "Saved. Restart MarketHub to apply.";
        msg.className = "hint ok";
        refresh();
      } else {
        msg.textContent = d.error || "Failed to save application settings.";
        msg.className = "hint err";
      }
    } catch {
      msg.textContent = "Network error saving application settings.";
      msg.className = "hint err";
    } finally { saveBtn.disabled = false; }
  });

  refresh();
}
