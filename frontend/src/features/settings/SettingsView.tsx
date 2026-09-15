import { useNavigate, useParams } from "react-router-dom";
import { Tabs } from "@/components/ui";
import { SETTINGS_SECTIONS } from "./constants";
import type { SettingsSection } from "./types";
import { GeneralPanel } from "./components/GeneralPanel";
import { BrokersPanel } from "./components/BrokersPanel";
import { NewsSourcesPanel } from "./components/NewsSourcesPanel";
import { AiMcpPanel } from "./components/AiMcpPanel";
import { DataRetentionPanel } from "./components/DataRetentionPanel";
import { LoggingPanel } from "./components/LoggingPanel";
import { BackupPanel } from "./components/BackupPanel";
import { AlertsSettingsPanel } from "./components/AlertsSettingsPanel";

export function SettingsView() {
  const { section: raw } = useParams();
  const navigate = useNavigate();
  const section: SettingsSection =
    SETTINGS_SECTIONS.find((s) => s.value === raw)?.value ?? "general";

  return (
    <div className="settings-page">
      <Tabs
        tabs={SETTINGS_SECTIONS}
        active={section}
        onChange={(s) => navigate(`/settings/${s}`)}
      />

      <div className="settings-content">
        {section === "general" ? <GeneralPanel /> : null}
        {section === "brokers" ? <BrokersPanel /> : null}
        {section === "news-sources" ? <NewsSourcesPanel /> : null}
        {section === "ai-mcp" ? <AiMcpPanel /> : null}
        {section === "data-retention" ? <DataRetentionPanel /> : null}
        {section === "logging" ? <LoggingPanel /> : null}
        {section === "backup" ? <BackupPanel /> : null}
        {section === "alerts" ? <AlertsSettingsPanel /> : null}
      </div>
    </div>
  );
}
