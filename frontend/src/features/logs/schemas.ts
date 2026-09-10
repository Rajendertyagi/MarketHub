import { z } from "zod";

// Zod schema for the Logs contracts. Fails loudly so a backend/frontend
// mismatch surfaces as a typed error instead of `undefined` fields.
export const logRecordSchema = z
  .object({
    ts: z.string(),
    level: z.string(),
    logger: z.string(),
    message: z.string(),
    event: z.string().nullable().optional(),
    request_id: z.string().nullable().optional(),
    consumer_id: z.string().nullable().optional(),
    alert_id: z.string().nullable().optional(),
    event_id: z.string().nullable().optional(),
    broker: z.string().nullable().optional(),
    exception: z.string().nullable().optional(),
    extra: z.record(z.unknown()).nullable().optional(),
  })
  .passthrough();

export const logsResponseSchema = z.object({
  status: z.string(),
  count: z.number(),
  records: z.array(logRecordSchema),
});
