import { z } from "zod";

// Zod schemas for the AI Alerts + MCP Tools read-only contracts. They fail
// loudly so a backend/frontend mismatch surfaces as a typed error.

const conditionAlertSchema = z.object({
  alert_id: z.string(),
  consumer_id: z.string(),
  name: z.string().nullable(),
  enabled: z.boolean(),
  trigger_mode: z.string(),
  condition_summary: z.string(),
  condition_json: z.record(z.unknown()).nullable(),
  instrument: z.string().nullable(),
  created_at: z.string().nullable(),
  updated_at: z.string().nullable(),
  last_triggered_at: z.string().nullable(),
  trigger_count: z.number(),
  metadata: z.record(z.unknown()).nullable(),
  current_state: z.string(),
  crossing_side: z.string(),
  state_updated_at: z.string().nullable(),
});

const triggeredEventSchema = z.object({
  event_id: z.union([z.number(), z.string()]),
  sequence: z.number(),
  alert_id: z.string(),
  source: z.string(),
  trigger_time: z.string(),
  created_at: z.string(),
  consumer_id: z.string(),
  condition_summary: z.string(),
  instrument: z.string(),
  delivery_state: z.enum(["acknowledged", "pending", "persisted"]),
  delivered_at: z.string().nullable(),
  acknowledged_at: z.string().nullable(),
});

const consumerStatusSchema = z.object({
  consumer_id: z.string(),
  created_at: z.string().nullable(),
  pending_count: z.number(),
  last_triggered: z
    .object({
      event_id: z.union([z.number(), z.string()]),
      trigger_time: z.string(),
      alert_id: z.string(),
    })
    .nullable(),
  last_checkpoint: z.object({ last_sequence: z.number(), updated_at: z.string() }).nullable(),
  unacknowledged_count: z.number(),
});

const mcpToolSchema = z.object({
  name: z.string(),
  title: z.string(),
  description: z.string(),
  category: z.string(),
  input_schema: z.record(z.unknown()),
});

export const aiAlertsResponseSchema = z.object({
  alerts: z.array(conditionAlertSchema),
  count: z.number(),
});

export const triggeredEventsResponseSchema = z.object({
  events: z.array(triggeredEventSchema),
  count: z.number(),
});

export const consumersResponseSchema = z.object({
  consumers: z.array(consumerStatusSchema),
  count: z.number(),
});

export const mcpToolsResponseSchema = z.object({
  tools: z.array(mcpToolSchema),
  count: z.number(),
});
