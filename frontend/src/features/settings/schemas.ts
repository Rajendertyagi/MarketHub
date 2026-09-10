import { z } from "zod";

// Zod schemas for the Settings contracts. They fail loudly so a backend/frontend
// mismatch surfaces as a typed `parse` error instead of `undefined` fields.
// Where the full backend envelope is not fully enumerated here (and the legacy
// JS is the only source of truth), schemas use `.passthrough()` so new fields
// never break validation.

export const appSettingsSchema = z
  .object({
    public_base_url: z.string().nullable().optional(),
    fyers_callback_url: z.string().nullable().optional(),
  })
  .passthrough();

export const upstoxAuthStatusSchema = z
  .object({
    authenticated: z.boolean().optional(),
    login_required: z.boolean().optional(),
    oauth_available: z.boolean().optional(),
    auth_code_pending: z.boolean().optional(),
    auth_state: z.string().optional(),
    state: z.string().optional(),
    expires_at: z.string().nullable().optional(),
    expired: z.boolean().optional(),
    expiry_known: z.boolean().optional(),
    session_restored: z.boolean().optional(),
    session_persisted: z.boolean().optional(),
    configured: z.boolean().optional(),
    feed_configured: z.boolean().optional(),
    restart_recovery: z.boolean().optional(),
  })
  .passthrough();

export const upstoxCredStatusSchema = z
  .object({
    api_key_configured: z.boolean().optional(),
    api_secret_configured: z.boolean().optional(),
    store_error: z.string().nullable().optional(),
    oauth_available: z.boolean().optional(),
  })
  .passthrough();

export const upstoxFeedConfigSchema = z
  .object({
    configured: z.boolean(),
    enabled: z.boolean(),
    type: z.string().nullable().optional(),
    instruments: z.array(z.unknown()).optional(),
    registered: z.boolean().optional(),
    restart_required: z.boolean().optional(),
  })
  .passthrough();

export const fyersSettingsSchema = z
  .object({
    app_id_configured: z.boolean().optional(),
    secret_configured: z.boolean().optional(),
    store_error: z.string().nullable().optional(),
    login_available: z.boolean().optional(),
    access_token_active: z.boolean().optional(),
    source_state: z.string().optional(),
    source_registered: z.boolean().optional(),
    source_enabled: z.boolean().optional(),
    access_token_expires_at: z.string().nullable().optional(),
    restart_recovery: z.boolean().optional(),
    login_required: z.boolean().optional(),
    session_restored: z.boolean().optional(),
  })
  .passthrough();

export const newsSourceConfigSchema = z
  .object({
    url: z.string().optional(),
    subreddit: z.string().optional(),
  })
  .passthrough();

export const newsSourceSchema = z.object({
  source_id: z.string(),
  name: z.string(),
  source_type: z.string(),
  category: z.string().nullable().optional(),
  enabled: z.boolean(),
  config_json: newsSourceConfigSchema.nullable().optional(),
});

export const newsSourcesResponseSchema = z.object({
  sources: z.array(newsSourceSchema),
});

export const testSourceResultSchema = z
  .object({
    reachable: z.boolean().optional(),
    message: z.string().optional(),
    sample_titles: z.array(z.string()).optional(),
  })
  .passthrough();

export const sourceTransitionSchema = z
  .object({
    at: z.string().optional(),
    from: z.string().optional(),
    to: z.string().optional(),
    reason: z.string().optional(),
  })
  .passthrough();

export const marketSourceSchema = z
  .object({
    name: z.string(),
    provider: z.string().optional(),
    state: z.string().optional(),
    task_running: z.boolean().nullable().optional(),
    mode: z.string().optional(),
    configured_instruments: z.number().optional(),
    subscribed_instruments: z.number().nullable().optional(),
    connect_attempts: z.number().optional(),
    reconnect_count: z.number().optional(),
    reconnecting: z.boolean().optional(),
    frames_received: z.number().optional(),
    malformed_frames: z.number().optional(),
    last_connected_at: z.string().nullable().optional(),
    last_message_at: z.string().nullable().optional(),
    last_error: z.string().nullable().optional(),
    last_exit_reason: z.string().nullable().optional(),
    last_exit_at: z.string().nullable().optional(),
    stop_reason: z.string().nullable().optional(),
    not_ready_reason: z.string().nullable().optional(),
    recent_transitions: z.array(sourceTransitionSchema).optional(),
  })
  .passthrough();

export const sourcesStatusResponseSchema = z.object({
  sources: z.array(marketSourceSchema),
});

export const chatStatusSchema = z
  .object({
    endpoint: z.string().optional(),
    model: z.string().optional(),
  })
  .passthrough();

export const sourceControlResultSchema = z
  .object({
    ok: z.boolean().optional(),
    result: z.string().optional(),
    reason: z.string().optional(),
    detail: z.string().optional(),
    was_running: z.boolean().optional(),
    stop_reason: z.string().optional(),
  })
  .passthrough();

// Generic action envelope returned by most control endpoints.
export const actionResultSchema = z
  .object({
    status: z.string().optional(),
    configured: z.boolean().optional(),
    removed: z.boolean().optional(),
  })
  .passthrough();
