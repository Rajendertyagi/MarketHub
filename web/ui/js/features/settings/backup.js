/**
 * MarketHub WebUI — Settings → Backup (database backup control).
 *
 * Owns the backup button + status message. Behavior preserved exactly:
 * one POST per click, backend-defined semantics, no new backup features.
 */

import { $ } from "../../utils.js";

export function initBackupSettings() {
  const btn = $("db-backup");
  if (!btn) return;
  const msg = $("backup-message");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const res = await fetch("/api/admin/backup", { method: "POST" });
      const d = await res.json();
      if (res.ok) {
        msg.textContent = "Backup saved to data/backups/" + d.file +
          " (contains ciphertext only; master.key required to decrypt).";
        msg.className = "hint ok";
      } else {
        msg.textContent = d.error || "Backup failed.";
        msg.className = "hint err";
      }
    } catch {
      msg.textContent = "Network error during backup.";
      msg.className = "hint err";
    } finally { btn.disabled = false; }
  });
}
