// Data hooks for the Test Center. The check list is a react-query read; running
// a suite is a mutation that returns the DiagnosticRunResult for rendering.
import { useMutation, useQuery } from "@tanstack/react-query";
import { getDiagnosticsChecks, runDiagnostics } from "./api";
import type { DiagnosticMode, DiagnosticRunResult } from "./types";

export function useDiagnosticsChecks() {
  return useQuery({
    queryKey: ["diagnostics", "checks"],
    queryFn: ({ signal }) => getDiagnosticsChecks(signal),
  });
}

export function useRunDiagnostics() {
  return useMutation({
    mutationFn: ({
      mode,
      symbol,
    }: {
      mode: DiagnosticMode;
      symbol: string;
    }): Promise<DiagnosticRunResult> => runDiagnostics(mode, symbol),
  });
}
