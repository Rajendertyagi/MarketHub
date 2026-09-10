import { useState } from "react";
import { DEFAULT_SYMBOL } from "./constants";
import type { DiagnosticMode, DiagnosticRunResult } from "./types";
import { useRunDiagnostics } from "./useDiagnostics";
import { DiagnosticsControls } from "./components/DiagnosticsControls";
import { RunResults } from "./components/RunResults";
import { ChecksList } from "./components/ChecksList";

export function DiagnosticsView() {
  const [mode, setMode] = useState<DiagnosticMode>("quick");
  const [symbol, setSymbol] = useState(DEFAULT_SYMBOL);
  const [result, setResult] = useState<DiagnosticRunResult | null>(null);
  const run = useRunDiagnostics();

  const onRun = () => {
    setResult(null);
    run.mutate(
      { mode, symbol },
      { onSuccess: (res) => setResult(res) },
    );
  };

  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">Test Center</h1>
        <span className="muted">
          Runs real endpoint/MCP checks server-side — React only displays
        </span>
      </div>

      <DiagnosticsControls
        mode={mode}
        symbol={symbol}
        onModeChange={setMode}
        onSymbolChange={setSymbol}
        onRun={onRun}
        running={run.isPending}
      />

      {run.isError ? (
        <p className="hint err">
          {(run.error as Error).message || "Diagnostics run failed"}
        </p>
      ) : null}

      {result ? <RunResults result={result} /> : null}

      <ChecksList />
    </div>
  );
}
