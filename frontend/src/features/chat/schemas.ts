// Zod contracts for Chat. The status endpoint is validated; streamed SSE events
// are parsed permissively (backend owns the event schema).
import { z } from "zod";

export const chatStatusSchema = z.object({
  configured: z.boolean(),
  endpoint: z.string().optional(),
  model: z.string().optional(),
});

export const chatEventSchema = z
  .object({
    type: z.string(),
    text: z.string().optional(),
    name: z.string().optional(),
    message: z.string().optional(),
  })
  .passthrough();

export type ChatStatus = z.infer<typeof chatStatusSchema>;
export type ChatEvent = z.infer<typeof chatEventSchema>;
