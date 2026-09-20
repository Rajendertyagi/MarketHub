// Thin API layer for MCP Tools. Read-only; every call goes through the shared
// `request()` helper (owns the /api prefix + Zod validation).
import { request } from "@/api/client";
import { mcpToolsResponseSchema } from "./schemas";
import type { McpToolsResponse } from "./types";

const MCP_TOOLS = "/mcp/tools";

export async function getMcpTools(signal?: AbortSignal): Promise<McpToolsResponse> {
  return request<McpToolsResponse>(MCP_TOOLS, {
    schema: mcpToolsResponseSchema,
    signal,
  });
}
