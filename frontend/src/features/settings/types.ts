// Domain types for the Settings feature. These mirror the canonical backend
// contracts (api/routes.py, api/product_routes.py, api/news_routes.py,
// api/chat_routes.py). React only renders and issues control actions; all
// credential/auth/feed semantics stay server-side (app/auth/*, source manager).

// ── General application settings (GET/POST /api/settings/app) ────────────────
export interface AppSettings {
  public_base_url: string | null;
  fyers_callback_url: string | null;
}

// ── Upstox auth status (GET /api/auth/upstox/status) ────────────────────────
export interface UpstoxAuthStatus {
  authenticated?: boolean;
  login_required?: boolean;
  oauth_available?: boolean;
  auth_code_pending?: boolean;
  auth_state?: string;
  state?: string;
  expires_at?: string | null;
  expired?: boolean;
  expiry_known?: boolean;
  session_restored?: boolean;
  session_persisted?: boolean;
  configured?: boolean;
  feed_configured?: boolean;
  restart_recovery?: boolean;
}

// ── Upstox app credentials (GET /api/settings/upstox) ───────────────────────
export interface UpstoxCredStatus {
  api_key_configured?: boolean;
  api_secret_configured?: boolean;
  store_error?: string | null;
  oauth_available?: boolean;
}

// ── Upstox feed config (GET|POST|DELETE /api/settings/upstox/feed) ──────────
export interface UpstoxFeedConfig {
  configured: boolean;
  enabled: boolean;
  type?: string | null;
  instruments?: unknown[];
  registered?: boolean;
  restart_required?: boolean;
}

// ── Fyers settings (GET /api/settings/fyers, plus auth status) ──────────────
export interface FyersSettings {
  app_id_configured?: boolean;
  secret_configured?: boolean;
  store_error?: string | null;
  login_available?: boolean;
  // Canonical auth projection (backend-owned; mirrors Upstox semantics).
  authenticated?: boolean;
  auth_state?: "authenticated" | "expired" | "missing" | "unknown" | string;
  expired?: boolean | null;
  expiry_known?: boolean;
  // Legacy presence signal: a token string exists. NOT a usability claim —
  // gating must use `authenticated`/`login_required`, never this.
  access_token_active?: boolean;
  source_state?: string;
  source_registered?: boolean;
  source_enabled?: boolean;
  access_token_expires_at?: string | null;
  restart_recovery?: boolean;
  login_required?: boolean;
  session_restored?: boolean;
  session_persisted?: boolean;
  stored_access_present?: boolean;
}

// ── Generic save/delete credential result ───────────────────────────────────
export interface SaveResult {
  configured?: boolean;
  removed?: boolean;
  [key: string]: unknown;
}

// ── News sources (GET/POST /api/news/sources, etc.) ─────────────────────────
export type NewsSourceType = "rss" | "reddit" | (string & {});

export interface NewsSourceConfig {
  url?: string;
  subreddit?: string;
  [key: string]: unknown;
}

export interface NewsSource {
  source_id: string;
  name: string;
  source_type: NewsSourceType;
  category?: string | null;
  enabled: boolean;
  config_json?: NewsSourceConfig | null;
}

export interface NewsSourcesResponse {
  sources: NewsSource[];
}

export interface TestSourceInput {
  source_type: NewsSourceType;
  config_json: NewsSourceConfig;
}

export interface TestSourceResult {
  reachable?: boolean;
  message?: string;
  sample_titles?: string[];
}

// ── Market sources (GET /api/sources/status, POST /api/sources/{name}/{action})
export interface SourceTransition {
  at?: string;
  from?: string;
  to?: string;
  reason?: string;
}

export interface MarketSource {
  name: string;
  provider?: string;
  state?: string;
  task_running?: boolean | null;
  mode?: string;
  configured_instruments?: number;
  subscribed_instruments?: number | null;
  connect_attempts?: number;
  reconnect_count?: number;
  reconnecting?: boolean;
  frames_received?: number;
  malformed_frames?: number;
  last_connected_at?: string | null;
  last_message_at?: string | null;
  last_error?: string | null;
  last_exit_reason?: string | null;
  last_exit_at?: string | null;
  stop_reason?: string | null;
  not_ready_reason?: string | null;
  recent_transitions?: SourceTransition[];
}

export interface SourcesStatusResponse {
  sources: MarketSource[];
}

export interface SourceControlResult {
  ok: boolean;
  result?: string;
  reason?: string;
  detail?: string;
  was_running?: boolean;
  stop_reason?: string;
}

// ── AI / MCP (GET /api/chat/status, POST /api/chat/config) ──────────────────
export interface ChatStatus {
  endpoint?: string;
  model?: string;
  [key: string]: unknown;
}

export interface ChatConfigInput {
  endpoint: string;
  model: string;
  api_key: string;
}

// ── Backup (POST /api/admin/backup) ─────────────────────────────────────────
export interface BackupResult {
  file?: string;
  [key: string]: unknown;
}

export type SettingsSection =
  | "general"
  | "brokers"
  | "news-sources"
  | "market-sources"
  | "ai-mcp"
  | "data-retention"
  | "logging"
  | "backup"
  | "alerts";
