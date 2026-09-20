import { Button } from "@/components/ui";
import { BROKER_UPSTOX } from "../constants";
import {
  formatFeedState,
  formatInstruments,
  formatStopReason,
  formatTimestamp,
  formatTransition,
} from "../format";
import type { MarketSource } from "../types";
import { useSourceControl } from "../useSettings";

interface SourceDetailProps {
  source: MarketSource | undefined;
  feedEnabled?: boolean;
  onFeedToggle?: () => void;
  feedBusy?: boolean;
}

export function SourceDetail({ source, feedEnabled, onFeedToggle, feedBusy }: SourceDetailProps) {
  const { control } = useSourceControl();
  if (!source) {
    return <em>Source not configured</em>;
  }
  const state = source.state ?? "stopped";
  const active = ["streaming", "connecting", "authorizing", "reconnecting"].includes(state);
  const ready = (source.provider ?? "") !== BROKER_UPSTOX || state !== "auth_required";

  const rows: [string, string][] = [
    ["Provider", source.provider ?? "—"],
    ["Feed State", formatFeedState(state)],
    ["Task Running", source.task_running == null ? "—" : source.task_running ? "Yes" : "No"],
    ["Mode", source.mode ?? "—"],
    ["Instruments", formatInstruments(source)],
    ["Connect Attempts", String(source.connect_attempts ?? 0)],
    [
      "Reconnects",
      `${source.reconnect_count ?? 0}${source.reconnecting ? " (reconnecting now)" : ""}`,
    ],
    ["Frames Received", String(source.frames_received ?? 0)],
    ["Malformed Frames", String(source.malformed_frames ?? 0)],
    ["Last Connected", formatTimestamp(source.last_connected_at)],
    ["Last Message", formatTimestamp(source.last_message_at)],
    ["Last Error", source.last_error ?? "—"],
    [
      "Last Exit",
      `${source.last_exit_reason ?? "—"}${source.last_exit_at ? ` at ${source.last_exit_at}` : ""}`,
    ],
    ["Stop Reason", formatStopReason(source.stop_reason)],
  ];

  return (
    <table className="data-table table-compact">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td className="market-source-prop">{k}</td>
            <td>{v}</td>
          </tr>
        ))}
        {source.not_ready_reason ? (
          <tr>
            <td className="market-source-prop">Not Ready</td>
            <td>{source.not_ready_reason}</td>
          </tr>
        ) : null}
        {source.recent_transitions?.length ? (
          <tr>
            <td className="market-source-transitions-label">Recent Transitions</td>
            <td>
              {source.recent_transitions
                .slice(-8)
                .reverse()
                .map((t) => (
                  <div
                    className="market-source-meta"
                    key={`${t.at ?? ""}-${t.from ?? ""}-${t.to ?? ""}`}
                  >
                    {formatTransition(t)}
                  </div>
                ))}
            </td>
          </tr>
        ) : null}
        <tr>
          <td className="market-source-prop">Controls</td>
          <td>
            <div className="control-row">
              {ready ? (
                <Button
                  className="btn btn-compact"
                  disabled={active || feedBusy}
                  onClick={() => control(source.name, "start")}
                >
                  Start Feed
                </Button>
              ) : null}
              {active ? (
                <>
                  <Button
                    className="btn btn-compact"
                    onClick={() => control(source.name, "restart")}
                  >
                    Restart Feed
                  </Button>
                  <Button className="btn btn-compact" onClick={() => control(source.name, "stop")}>
                    Stop Feed
                  </Button>
                </>
              ) : null}
              {onFeedToggle ? (
                <Button
                  className={`btn btn-compact ${feedEnabled ? "btn-outline-danger" : ""}`}
                  disabled={feedBusy}
                  onClick={onFeedToggle}
                >
                  {feedEnabled ? "Disable Feed" : "Enable Feed"}
                </Button>
              ) : null}
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  );
}
