import { useState } from "react";
import { Button } from "@/components/ui";
import { useBackup } from "../useSettings";

export function BackupPanel() {
  const backup = useBackup();
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  async function onBackup() {
    setMsg(null);
    try {
      const res = (await backup.mutateAsync()) as { file?: string };
      setMsg({
        kind: "ok",
        text: `Backup saved to data/backups/${res.file ?? ""} (contains ciphertext only; master.key required to decrypt).`,
      });
    } catch (e) {
      setMsg({
        kind: "err",
        text: e instanceof Error ? e.message : "Backup failed.",
      });
    }
  }

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Backup</h2>
      </div>
      <div className="setting-row">
        <span className="auth-label">Database Backup</span>
        <Button onClick={onBackup} disabled={backup.isPending}>
          {backup.isPending ? "Backing up…" : "Backup Database"}
        </Button>
      </div>
      {msg ? <p className={`hint ${msg.kind}`}>{msg.text}</p> : null}
    </div>
  );
}
