import { useState } from "react";
import { Tabs } from "@/components/ui";
import { SETTINGS_SECTIONS } from "./constants";
import type { SettingsSection } from "./types";
import { GeneralPanel } from "./components/GeneralPanel";
import { BrokersPanel } from "./components/BrokersPanel";
import { NewsSourcesPanel } from "./components/NewsSourcesPanel";
import { MarketSourcesPanel } from "./components/MarketSourcesPanel";
import { AiMcpPanel } from "./components/AiMcpPanel";
import { DataRetentionPanel } from "./components/DataRetentionPanel";
import { LoggingPanel } from "./components/LoggingPanel";
import { BackupPanel } from "./components/BackupPanel";
import { AlertsSettingsPanel } from "./components/AlertsSettingsPanel";

export function SettingsView() {
  const [section, setSection] = useState<SettingsSection>("general");

  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">Settings</h1>
        <span className="muted">Application, broker, source &amp; AI configuration</span>
      </div>

      <Tabs
        tabs={SETTINGS_SECTIONS}
        active={section}
        onChange={setSection}
      />

      <div className="settings-content">
        {section === "general" ? <GeneralPanel /> : null}
        {section === "brokers" ? <BrokersPanel /> : null}
        {section === "news-sources" ? <NewsSourcesPanel /> : null}
        {section === "market-sources" ? <MarketSourcesPanel /> : null}
        {section === "ai-mcp" ? <AiMcpPanel /> : null}
        {section === "data-retention" ? <DataRetentionPanel /> : null}
        {section === "logging" ? <LoggingPanel /> : null}
        {section === "backup" ? <BackupPanel /> : null}
        {section === "alerts" ? <AlertsSettingsPanel /> : null}
      </div>
    </div>
  );
}
