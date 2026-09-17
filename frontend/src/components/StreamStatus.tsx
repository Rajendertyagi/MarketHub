// Shared live-stream connection badge. Any view fed by an SSE stream renders
// this (instead of inventing its own copy) so "live vs reconnecting" reads
// identically everywhere. Purely presentational: the owning hook owns the
// boolean, this only labels it honestly.
export function StreamStatus({ connected }: { connected: boolean }) {
  return (
    <span
      className={`stream-status ${connected ? "stream-status-on" : "stream-status-off"}`}
      role="status"
      aria-live="polite"
      aria-label={connected ? "Live market stream connected" : "Market stream reconnecting"}
    >
      {connected ? "● Live" : "● Reconnecting"}
    </span>
  );
}
