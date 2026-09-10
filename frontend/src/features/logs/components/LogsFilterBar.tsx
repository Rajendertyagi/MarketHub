import { Button, Field, Input, Select } from "@/components/ui";
import { LOG_LEVELS } from "../constants";
import type { LogFilters } from "../types";

export interface LogsFilterBarProps {
  draft: LogFilters;
  onChange: (next: LogFilters) => void;
  onApply: () => void;
  onClear: () => void;
  paused: boolean;
  onTogglePause: () => void;
  autoFollow: boolean;
  onToggleAutoFollow: () => void;
  connected: boolean;
  count: number;
}

// Filter + transport controls for the Logs view. All values are controlled by
// the parent; nothing here touches the network directly.
export function LogsFilterBar({
  draft,
  onChange,
  onApply,
  onClear,
  paused,
  onTogglePause,
  autoFollow,
  onToggleAutoFollow,
  connected,
  count,
}: LogsFilterBarProps) {
  return (
    <div className="card logs-filterbar">
      <div className="control-row">
        <Field label="Level">
          <Select
            value={draft.level ?? ""}
            onChange={(e) => onChange({ ...draft, level: e.target.value })}
          >
            <option value="">All</option>
            {LOG_LEVELS.map((lvl) => (
              <option key={lvl} value={lvl}>
                {lvl}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Component">
          <Input
            placeholder="logger substring"
            value={draft.logger ?? ""}
            onChange={(e) => onChange({ ...draft, logger: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter") onApply();
            }}
          />
        </Field>
        <Field label="Search">
          <Input
            placeholder="message text"
            value={draft.search ?? ""}
            onChange={(e) => onChange({ ...draft, search: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter") onApply();
            }}
          />
        </Field>
        <Button variant="primary" onClick={onApply}>
          Apply
        </Button>
        <Button className="btn-compact" onClick={onClear}>
          Clear
        </Button>
        <Button className="btn-compact" onClick={onTogglePause}>
          {paused ? "Resume" : "Pause"}
        </Button>
        <label className="logs-autofollow">
          <input
            type="checkbox"
            checked={autoFollow}
            onChange={onToggleAutoFollow}
          />{" "}
          Auto-follow
        </label>
        <span
          className={`logs-conn ${connected ? "logs-conn-on" : "logs-conn-off"}`}
        >
          {connected ? "● Connected" : "● Reconnecting"}
        </span>
        <span className="muted">{count} records</span>
      </div>
    </div>
  );
}
