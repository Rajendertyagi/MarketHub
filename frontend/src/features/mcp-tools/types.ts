// Domain types for the MCP Tools browser. Mirrors the read-only contract in
// api/ai_alert_routes.py (GET /api/mcp/tools). React only lists registered
// tools from the canonical registry — no tool execution from the UI.

export interface McpTool {
  name: string;
  title: string;
  description: string;
  category: string;
  input_schema: Record<string, unknown>;
}

export interface McpToolsResponse {
  tools: McpTool[];
  count: number;
}
