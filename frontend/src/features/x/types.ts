// Domain types for the X/Twitter feature. Mirror the backend contracts
// (api/x_routes.py). React only renders and issues control actions; all
// credential/poll/feed semantics stay server-side (x_twitter/service.py).

export interface XCredStatus {
  configured?: boolean;
  auth_state?: string | null;
  auth_reason?: string | null;
  auth_at?: string | null;
}

export interface XPollConfig {
  enabled: boolean;
  poll_interval_seconds: number;
  default_limit: number;
  retention_days: number;
  cli_path?: string | null;
  proxy?: string | null;
  request_delay?: number | null;
  updated_at?: string | null;
}

export interface XStatus {
  auth?: {
    state?: string | null;
    reason?: string | null;
    at?: string | null;
  };
  enabled?: boolean;
  poll_interval_seconds?: number | null;
  default_limit?: number | null;
  cli_path?: string | null;
  cli_pinned?: string | null;
  cursor_set?: boolean;
  cached_tweets?: number;
  last_fetch_at?: string | null;
  last_new_count?: number | null;
  last_error?: string | null;
}

export interface XTestConnection {
  status?: string;
  state?: string | null;
  reason?: string | null;
  checked_at?: string | null;
}

export interface XTweet {
  id: string;
  handle?: string | null;
  author_name?: string | null;
  text?: string | null;
  url?: string | null;
  metrics?: Record<string, number> | null;
  is_retweet?: boolean;
  posted_at?: string | null;
  fetched_at?: string | null;
}

export interface XPollConfigInput {
  enabled?: boolean;
  poll_interval_seconds?: number;
  default_limit?: number;
  retention_days?: number;
  proxy?: string;
  request_delay?: number;
  cli_path?: string;
}
