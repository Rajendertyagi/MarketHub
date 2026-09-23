// Pure presentation helpers for the Settings feature. Kept separate from
// data/logic so components stay declarative and formatting is unit-testable.
import { FEED_STATE_LABELS, FEED_STATE_SHORT_LABELS, STOP_REASON_LABELS } from "./constants";
import type { FyersSettings, MarketSource, UpstoxAuthStatus } from "./types";

// Friendly, long-form feed-state label (Settings detail tables).
export function formatFeedState(state?: string): string {
  if (!state) return "—";
  return FEED_STATE_LABELS[state] ?? state;
}

// Compact topbar-style label.
export function formatFeedStateShort(state?: string): string {
  if (!state) return "—";
  return FEED_STATE_SHORT_LABELS[state] ?? state;
}

// Human-readable stop reason (backend-enumerated).
export function formatStopReason(reason?: string | null): string {
  if (!reason) return "—";
  if (reason.startsWith("terminal:")) return "Terminal failure (non-retryable)";
  if (reason.startsWith("error:")) return "Internal error";
  return STOP_REASON_LABELS[reason] ?? reason;
}

// Upstox auth chip label + css class (mirrors legacy auth.js state machine).
export function formatUpstoxAuthChip(s: UpstoxAuthStatus): { label: string; cls: string } {
  if (s.oauth_available === false) {
    return { label: "Credentials Missing", cls: "chip chip-off" };
  }
  if (s.authenticated === true) {
    return { label: "Authenticated", cls: "chip chip-on" };
  }
  switch (s.auth_state) {
    case "expired":
      return { label: "Session expired", cls: "chip chip-off" };
    case "rejected":
      return { label: "Session needs login", cls: "chip chip-off" };
    default:
      return { label: "Login required", cls: "chip chip-off" };
  }
}

// Fyers auth chip label + css class. Driven ONLY by the backend canonical
// projection (authenticated/auth_state) — never by token presence. Unknown
// and loading are never rendered as authenticated.
export function formatFyersAuthChip(s: FyersSettings): { label: string; cls: string } {
  if (s.authenticated === true) {
    return { label: "Connected", cls: "chip chip-on" };
  }
  switch (s.auth_state) {
    case "expired":
      return { label: "Session expired — re-login", cls: "chip chip-off" };
    case "missing":
      return s.login_available
        ? { label: "Login required", cls: "chip chip-off" }
        : { label: "Credentials required", cls: "chip chip-off" };
    case "unknown":
      return { label: "Status unknown — login available", cls: "chip chip-off" };
    default:
      return { label: "Login required", cls: "chip chip-off" };
  }
}

// Chip class for an on/off/warn boolean-ish state.
export function formatOnOffChip(
  on: boolean,
  onLabel = "On",
  offLabel = "Off",
): { label: string; cls: string } {
  return on ? { label: onLabel, cls: "chip chip-on" } : { label: offLabel, cls: "chip chip-off" };
}

// Render a recent transition (e.g. "12:30:45  stopped → streaming (reason)").
export function formatTransition(t: {
  at?: string;
  from?: string;
  to?: string;
  reason?: string;
}): string {
  const at = (t.at ?? "").replace("T", " ").slice(0, 19);
  const reason = t.reason ? ` (${t.reason})` : "";
  return `${at}  ${t.from ?? "?"} → ${t.to ?? "?"}  ${reason}`.trim();
}

// Compose the instruments line for a market source detail row.
export function formatInstruments(s: MarketSource): string {
  if (s.subscribed_instruments != null) {
    return `${s.configured_instruments ?? 0} desired / ${s.subscribed_instruments} subscribed`;
  }
  return `${s.configured_instruments ?? 0} desired`;
}

// Normalize ISO-ish timestamps for display.
export function formatTimestamp(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso.includes("T") ? iso : iso.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}
