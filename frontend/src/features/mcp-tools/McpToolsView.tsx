import { ToolsTable } from "./components/ToolsTable";

export function McpToolsView() {
  return (
    <div className="panel">
      <div className="page-header">
        <h1 className="page-title">MCP Tools</h1>
        <span className="muted">Registered tools from the canonical MCP registry (read-only)</span>
      </div>
      <ToolsTable />
    </div>
  );
}
