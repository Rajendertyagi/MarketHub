// Data hook for MCP Tools. Thin consumer of the API module; read-only.
import { useQuery } from "@tanstack/react-query";
import { getMcpTools } from "./api";
import { MCP_TOOLS_REFRESH_MS } from "./constants";

export function useMcpTools() {
  return useQuery({
    queryKey: ["mcp", "tools"],
    queryFn: ({ signal }) => getMcpTools(signal),
    refetchInterval: MCP_TOOLS_REFRESH_MS,
  });
}
