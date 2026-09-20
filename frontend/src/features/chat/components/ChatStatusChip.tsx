import type { ChatStatus } from "../types";

interface Props {
  status?: ChatStatus;
  loading?: boolean;
}

// Shows whether an AI provider is configured (read-only; configuration lives in
// Settings → AI Provider). Clearly states when chat is unavailable.
export function ChatStatusChip({ status, loading }: Props) {
  if (loading || !status) {
    return <span className="chip chip-off">Checking AI…</span>;
  }
  if (status.configured) {
    return (
      <span className="chip chip-on" title={status.endpoint ?? ""}>
        AI: {status.model ?? "configured"}
      </span>
    );
  }
  return (
    <span className="chip chip-off">AI not configured — set it in Settings → AI Provider</span>
  );
}
