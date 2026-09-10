import { Button } from "@/components/ui";
import { AsyncStateView } from "@/components/ui";
import { useSourceControl, useSourcesStatus } from "../useSettings";
import { BROKER_FYERS, BROKER_UPSTOX } from "../constants";
import {
  formatFeedState,
  formatInstruments,
  formatStopReason,
  formatTimestamp,
  formatTransition,
} from "../format";
import type { ApiError } from "@/types";
import type { MarketSource } from "../types";

function SourceDetail({ source }: { source: MarketSource | undefined }) {
  const { control } = useSourceControl();
  if (!source) {
    return <em>Source not configured</em>;
  }
  const state = source.state ?? "stopped";
  const active = ["streaming", "connecting", "authorizing", "reconnecting"].includes(
    state,
  );
  const ready = (source.provider ?? "") !== BROKER_UPSTOX || state !== "auth_required";

  const rows: [string, string][] = [
    ["Provider", source.provider ?? "—"],
    ["Feed State", formatFeedState(state)],
    ["Task Running", source.task_running == null ? "—" : source.task_running ? "Yes" : "No"],
    ["Mode", source.mode ?? "—"],
    ["Instruments", formatInstruments(source)],
    ["Connect Attempts", String(source.connect_attempts ?? 0)],
    ["Reconnects", `${source.reconnect_count ?? 0}${source.reconnecting ? " (reconnecting now)" : ""}`],
    ["Frames Received", String(source.frames_received ?? 0)],
    ["Malformed Frames", String(source.malformed_frames ?? 0)],
    ["Last Connected", formatTimestamp(source.last_connected_at)],
    ["Last Message", formatTimestamp(source.last_message_at)],
    ["Last Error", source.last_error ?? "—"],
    ["Last Exit", `${source.last_exit_reason ?? "—"}${source.last_exit_at ? ` at ${source.last_exit_at}` : ""}`],
    ["Stop Reason", formatStopReason(source.stop_reason)],
  ];

  return (
    <table className="data-table">
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
        {source.recent_transitions && source.recent_transitions.length ? (
          <tr>
            <td className="market-source-transitions-label">Recent Transitions</td>
            <td>
              {source.recent_transitions
                .slice(-8)
                .reverse()
                .map((t, i) => (
                  <div className="market-source-meta" key={i}>
                    {formatTransition(t)}
                  </div>
                ))}
            </td>
          </tr>
        ) : null}
        <tr>
          <td className="market-source-prop">Controls</td>
          <td>
            {ready ? (
              <Button
                className="btn"
                disabled={active}
                onClick={() => control(source.name, "start")}
              >
                Start Feed
              </Button>
            ) : null}{" "}
            {active ? (
              <>
                <Button className="btn" onClick={() => control(source.name, "restart")}>
                  Restart Feed
                </Button>{" "}
                <Button className="btn" onClick={() => control(source.name, "stop")}>
                  Stop Feed
                </Button>
              </>
            ) : null}
          </td>
        </tr>
      </tbody>
    </table>
  );
}

export function MarketSourcesPanel() {
  const { data, status, error, refetch } = useSourcesStatus();

  if (status === "pending") {
    return <AsyncStateView status="loading" loadingLabel="Loading market sources…" />;
  }
  if (status === "error") {
    return (
      <AsyncStateView status="error" error={error as ApiError} onRetry={() => refetch()} />
    );
  }

  const sources = data?.sources ?? [];
  const upstox = sources.find((s) => s.name === BROKER_UPSTOX);
  const fyers = sources.find((s) => s.name === BROKER_FYERS);

  return (
    <div className="panel">
      <div className="panel-header">
        <h2>Market Sources</h2>
      </div>
      <p className="form-hint">
        Live broker feed lifecycle. Status refreshes automatically; use Start / Stop
        / Restart per source. Disabling a feed is preferred over deletion.
      </p>
      <div className="panel-header">
        <h2>Upstox</h2>
      </div>
      <div className="panel-spaced">
        <SourceDetail source={upstox} />
      </div>
      <div className="panel-header panel-spaced">
        <h2>Fyers</h2>
      </div>
      <div className="panel-spaced">
        <SourceDetail source={fyers} />
      </div>
    </div>
  );
}
