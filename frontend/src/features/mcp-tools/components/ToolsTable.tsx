import { AsyncStateView } from "@/components/ui";
import { ApiError } from "@/types";
import { useMcpTools } from "../useMcpTools";

// Lists all registered MCP tools from the canonical registry. Read-only.
export function ToolsTable() {
  const { data, status, error, refetch } = useMcpTools();

  if (status === "pending") {
    return <AsyncStateView status="loading" loadingLabel="Loading tools…" />;
  }
  if (status === "error") {
    return (
      <AsyncStateView
        status="error"
        error={error as ApiError}
        onRetry={() => refetch()}
      />
    );
  }

  const tools = data?.tools ?? [];
  if (!tools.length) {
    return <p className="hint">No MCP tools registered.</p>;
  }

  return (
    <table className="table">
      <thead>
        <tr>
          <th>Name</th>
          <th>Title</th>
          <th>Category</th>
          <th>Description</th>
          <th>Input</th>
        </tr>
      </thead>
      <tbody>
        {tools.map((t) => (
          <tr key={t.name}>
            <td className="mono">{t.name}</td>
            <td>{t.title}</td>
            <td>
              <span className="ui-badge ui-badge-info">{t.category}</span>
            </td>
            <td className="mcp-tool-desc">{t.description}</td>
            <td>
              <details className="mcp-tool-schema">
                <summary>schema</summary>
                <pre>{JSON.stringify(t.input_schema, null, 2)}</pre>
              </details>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
