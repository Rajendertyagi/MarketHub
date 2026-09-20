import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { FnoWorkspaceView } from "./FnoWorkspaceView";

// Standalone Option Chain screen. Reuses the F&O Workspace (single source of
// truth for chain data) but deep-links to a sensible default (index underlying,
// chain tab) so the option chain is a first-class, discoverable screen rather
// than a buried tab. No duplicated data logic.
const DEFAULT_UNDERLYING = "NIFTY";
const DEFAULT_KIND = "index";

export function OptionChainView() {
  const [params, setParams] = useSearchParams();

  useEffect(() => {
    const next = new URLSearchParams(params);
    if (!next.get("sym")) next.set("sym", DEFAULT_UNDERLYING);
    if (!next.get("kind")) next.set("kind", DEFAULT_KIND);
    if (!next.get("tab")) next.set("tab", "chain");
    if (next.toString() !== params.toString()) {
      setParams(next, { replace: true });
    }
    // Idempotent: re-runs safely when params change, no-ops once seeded.
  }, [params, setParams]);

  return <FnoWorkspaceView />;
}
