import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Tabs } from "@/components/ui";
import { ConsumersPanel } from "./components/ConsumersPanel";
import { AlertsTable } from "./components/AlertsTable";
import { EventsTable } from "./components/EventsTable";

type AiTab = "consumers" | "alerts" | "events";

const TABS: { value: AiTab; label: string }[] = [
  { value: "consumers", label: "Consumers" },
  { value: "alerts", label: "Condition Alerts" },
  { value: "events", label: "Triggered Events" },
];

export function AiAlertsView() {
  const [tab, setTab] = useState<AiTab>("consumers");
  const qc = useQueryClient();

  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">AI Alerts</h1>
        <span className="muted">
          Read-only observability over durable alert state
        </span>
        <Button
          className="btn-compact"
          onClick={() =>
            qc.invalidateQueries({ queryKey: ["ai-alerts"] })
          }
        >
          Refresh
        </Button>
      </div>

      <Tabs<AiTab>
        tabs={TABS}
        active={tab}
        onChange={setTab}
      />

      {tab === "consumers" && <ConsumersPanel />}
      {tab === "alerts" && <AlertsTable />}
      {tab === "events" && <EventsTable />}
    </div>
  );
}
