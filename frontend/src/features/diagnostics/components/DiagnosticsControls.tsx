import { Button, Field, Input, Select } from "@/components/ui";
import { DIAGNOSTIC_MODES } from "../constants";
import type { DiagnosticMode } from "../types";

export interface DiagnosticsControlsProps {
  mode: DiagnosticMode;
  symbol: string;
  onModeChange: (mode: DiagnosticMode) => void;
  onSymbolChange: (symbol: string) => void;
  onRun: () => void;
  running: boolean;
}

// Run controls for the diagnostic suite. Controlled by the parent; triggers the
// run mutation via onRun.
export function DiagnosticsControls({
  mode,
  symbol,
  onModeChange,
  onSymbolChange,
  onRun,
  running,
}: DiagnosticsControlsProps) {
  return (
    <div className="card diag-controls">
      <div className="control-row">
        <Field label="Mode">
          <Select
            value={mode}
            onChange={(e) => onModeChange(e.target.value as DiagnosticMode)}
          >
            {DIAGNOSTIC_MODES.map((m) => (
              <option key={m} value={m}>
                {m === "quick" ? "Quick" : "Full"}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Symbol">
          <Input
            value={symbol}
            onChange={(e) => onSymbolChange(e.target.value)}
            placeholder="NIFTY"
          />
        </Field>
        <Button variant="primary" onClick={onRun} disabled={running}>
          {running ? "Running…" : "Run diagnostics"}
        </Button>
      </div>
    </div>
  );
}
