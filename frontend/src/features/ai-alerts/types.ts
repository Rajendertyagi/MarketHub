// Domain types for the AI Alerts observability feature. These mirror the
// read-only backend contracts in api/ai_alert_routes.py. React only renders
// durable alert state — no evaluation, no mutations.

export interface ConditionAlert {
  alert_id: string;
  consumer_id: string;
  name: string | null;
  enabled: boolean;
  trigger_mode: string;
  condition_summary: string;
  condition_json: Record<string, unknown> | null;
  instrument: string | null;
  created_at: string | null;
  updated_at: string | null;
  last_triggered_at: string | null;
  trigger_count: number;
  metadata: Record<string, unknown> | null;
  current_state: string;
  crossing_side: string;
  state_updated_at: string | null;
}

export interface TriggeredEvent {
  event_id: number | string;
  sequence: number;
  alert_id: string;
  source: string;
  trigger_time: string;
  created_at: string;
  consumer_id: string;
  condition_summary: string;
  instrument: string;
  delivery_state: "acknowledged" | "pending" | "persisted";
  delivered_at: string | null;
  acknowledged_at: string | null;
}

export interface ConsumerLastTriggered {
  event_id: number | string;
  trigger_time: string;
  alert_id: string;
}

export interface ConsumerCheckpoint {
  last_sequence: number;
  updated_at: string;
}

export interface ConsumerStatus {
  consumer_id: string;
  created_at: string | null;
  pending_count: number;
  last_triggered: ConsumerLastTriggered | null;
  last_checkpoint: ConsumerCheckpoint | null;
  unacknowledged_count: number;
}

export interface AiAlertsResponse {
  alerts: ConditionAlert[];
  count: number;
}

export interface TriggeredEventsResponse {
  events: TriggeredEvent[];
  count: number;
}

export interface ConsumersResponse {
  consumers: ConsumerStatus[];
  count: number;
}
