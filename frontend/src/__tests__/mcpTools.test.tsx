import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

const api = vi.hoisted(() => ({
  getMcpTools: vi.fn(),
}));
vi.mock("@/features/mcp-tools/api", () => ({
  getMcpTools: api.getMcpTools,
}));

import { McpToolsView } from "@/features/mcp-tools";

const TOOL = {
  name: "get_quote",
  title: "Get Quote",
  description: "Fetch the latest quote for an instrument.",
  category: "market",
  input_schema: { type: "object", properties: { symbol: { type: "string" } } },
};

function renderWithProviders(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/mcp"]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("McpToolsView", () => {
  it("renders registered tools from the canonical registry", async () => {
    api.getMcpTools.mockResolvedValue({ tools: [TOOL], count: 1 });
    renderWithProviders(<McpToolsView />);
    expect(await screen.findByText("MCP Tools")).toBeTruthy();
    expect(await screen.findByText("get_quote")).toBeTruthy();
    expect(await screen.findByText("Get Quote")).toBeTruthy();
    expect(await screen.findByText("market")).toBeTruthy();
    expect(
      await screen.findByText("Fetch the latest quote for an instrument."),
    ).toBeTruthy();
  });

  it("shows error state on load failure", async () => {
    api.getMcpTools.mockRejectedValue(new Error("mcp load failed"));
    renderWithProviders(<McpToolsView />);
    expect(await screen.findByText(/mcp load failed/)).toBeTruthy();
  });
});
