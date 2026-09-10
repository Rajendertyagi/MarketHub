import { z } from "zod";

// Zod schema for the MCP Tools read-only contract.
export const mcpToolSchema = z.object({
  name: z.string(),
  title: z.string(),
  description: z.string(),
  category: z.string(),
  input_schema: z.record(z.unknown()),
});

export const mcpToolsResponseSchema = z.object({
  tools: z.array(mcpToolSchema),
  count: z.number(),
});
