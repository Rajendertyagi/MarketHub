import { z } from "zod";

// Zod schemas for the Alerts contracts. They fail loudly so a backend/frontend
// mismatch surfaces as a typed `parse` error instead of `undefined` fields.

const alertFieldSchema = z.enum(["ltp", "change_percent", "volume", "oi_change_percent"]);
const alertOperatorSchema = z.enum([
  "gt",
  "lt",
  "crosses_above",
  "crosses_below",
]);

// SQLite returns `enabled` as INTEGER (1/0); normalize to boolean.
const enabledSchema = z.union([z.boolean(), z.number()]).transform((v) => Boolean(v));

export const marketAlertSchema = z.object({
  id: z.number(),
  exchange: z.string(),
  instrument_token: z.string(),
  tradingsymbol: z.string(),
  field: alertFieldSchema,
  operator: alertOperatorSchema,
  threshold: z.number(),
  enabled: enabledSchema,
  state: z.string(),
  created_at: z.string().nullable(),
  triggered_at: z.string().nullable(),
});

export const alertNotificationSchema = z.object({
  alert_id: z.number(),
  tradingsymbol: z.string(),
  field: alertFieldSchema,
  operator: alertOperatorSchema,
  threshold: z.number(),
  value: z.number().nullable(),
  ts: z.number(),
});

export const alertHistoryRowSchema = z.object({
  id: z.number(),
  alert_id: z.number(),
  exchange: z.string().nullable(),
  instrument_token: z.string().nullable(),
  tradingsymbol: z.string().nullable(),
  field: alertFieldSchema,
  operator: alertOperatorSchema,
  threshold: z.number().nullable(),
  observed_value: z.number().nullable(),
  provider: z.string().nullable(),
  triggered_at: z.string(),
  created_at: z.string(),
});

export const alertsResponseSchema = z.object({
  alerts: z.array(marketAlertSchema),
  notifications: z.array(alertNotificationSchema),
});

export const alertHistoryResponseSchema = z.object({
  history: z.array(alertHistoryRowSchema),
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
});

export const alertActionSchema = z.object({
  status: z.string(),
  alert: marketAlertSchema.optional(),
  deleted: z.union([z.string(), z.number()]).optional(),
});
