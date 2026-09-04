"""
SQLite persistence layer for persistent alerts.

Separate from MCP transport — this module knows nothing about subscriptions,
resources, or the MCP protocol. It only handles durable storage of events,
consumers, routing, acknowledgements, and checkpoints.

Schema evolution:
  v1 — id TEXT PRIMARY KEY, type, source, timestamp, data, created_at
  v2 — sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE
  v3 — add routing JSON column + consumers/consumer_topics/consumer_event_state
  v4 — remove redundant sequence from consumer_event_state, add FK to event_id
  v5 — add consumer_checkpoints, materialize per-consumer state at publish time
  v6 — add source_state (durable source cursors)
  v7 — add source_seen_items (durable restart-safe source deduplication)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.errors import (
    ConsumerNotFoundError,
    EventNotFoundError,
    EventNotRelevantError,
)
from core.persistence.modules import alerts as _alerts
from core.persistence.modules import condition_alerts as _condition_alerts
from core.persistence.modules import consumers as _consumers
from core.persistence.modules import delivery as _delivery
from core.persistence.modules import events as _events
from core.persistence.modules import news as _news
from core.persistence.modules import products as _products
from core.persistence.modules import recent_events as _recent_events
from core.persistence.modules import replay as _replay
from core.persistence.modules import retention as _retention
from core.persistence.modules import secrets as _secrets
from core.persistence.modules import source_state as _source_state
from core.persistence.modules.products import migrate_v10_to_v11, migrate_v11_to_v12
from core.persistence.modules.condition_alerts import migrate_v12_to_v13
from core.persistence.modules.news import migrate_v13_to_v14
from core.persistence.modules.news import migrate_v14_to_v15
from core.persistence.modules.schema import (
    SCHEMA_VERSION,
    create_v7_schema,
    get_schema_version,
    migrate_v1_to_v3,
    migrate_v2_to_v3,
    migrate_v3_to_v4,
    migrate_v4_to_v5,
    migrate_v5_to_v6,
    migrate_v6_to_v7,
    migrate_v7_to_v8,
    migrate_v8_to_v9,
)
from core.persistence.modules.secrets import migrate_v9_to_v10

logger = logging.getLogger(__name__)

# Maximum events a single replay/GetPending call can return.
MAX_REPLAY_LIMIT = 500


class EventStore:
    """Thread-safe SQLite backend for persistent events, consumers, routing, and checkpoints."""

    def __init__(self, db_path: str) -> None:
        self._db_path = str(Path(db_path).resolve())
        self._ensure_directory()
        self._init_db()

    # ─── Connection helper ────────────────────────────────────────────────────

    @staticmethod
    def _open(db_path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _ensure_directory(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

    # ─── Schema initialization & migration ───────────────────────────────────

    def _init_db(self) -> None:
        conn = self._open(self._db_path)
        try:
            current_version = get_schema_version(conn)

            if current_version == 0:
                # Fresh database: create the full current schema and set version
                create_v7_schema(conn)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                conn.commit()
                logger.info("event store initialized (fresh v%d): %s", SCHEMA_VERSION, self._db_path)
            elif current_version < SCHEMA_VERSION:
                # Migrate sequentially: one version at a time, re-reading version each step
                while current_version < SCHEMA_VERSION:
                    if current_version == 1:
                        migrate_v1_to_v3(conn)
                    elif current_version == 2:
                        migrate_v2_to_v3(conn)
                    elif current_version == 3:
                        migrate_v3_to_v4(conn)
                    elif current_version == 4:
                        migrate_v4_to_v5(conn)
                    elif current_version == 5:
                        migrate_v5_to_v6(conn)
                    elif current_version == 6:
                        migrate_v6_to_v7(conn)
                    elif current_version == 7:
                        migrate_v7_to_v8(conn)
                    elif current_version == 8:
                        migrate_v8_to_v9(conn)
                    elif current_version == 9:
                        migrate_v9_to_v10(conn)
                    elif current_version == 10:
                        migrate_v10_to_v11(conn)
                    elif current_version == 11:
                        migrate_v11_to_v12(conn)
                    elif current_version == 12:
                        migrate_v12_to_v13(conn)
                    elif current_version == 13:
                        migrate_v13_to_v14(conn)
                    elif current_version == 14:
                        migrate_v14_to_v15(conn)
                    else:
                        raise RuntimeError(
                            f"unsupported schema version {current_version}; "
                            f"expected between 1 and {SCHEMA_VERSION - 1}"
                        )
                    # Re-read version after migration (each migration sets its own target)
                    current_version = get_schema_version(conn)
                logger.info("event store migrated to v%d: %s", SCHEMA_VERSION, self._db_path)
            elif current_version == SCHEMA_VERSION:
                # Already current — ensure indexes exist (defensive idempotency)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idxCES_ack
                    ON consumer_event_state(consumer_id, acknowledged_at)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idxCES_consumer
                    ON consumer_event_state(consumer_id, event_id)
                """)
                conn.commit()
                logger.info("event store ready (v%d): %s", current_version, self._db_path)
            else:
                raise RuntimeError(
                    f"schema version {current_version} exceeds supported max {SCHEMA_VERSION}; "
                    "upgrade the application first"
                )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ─── Event persistence with materialization ───────────────────────────────

    def save(
        self,
        event_id: str,
        event_type: str,
        source: str,
        timestamp: str,
        data: dict[str, Any],
        routing: dict[str, Any] | None = None,
    ) -> int:
        """
        Persist a single event and materialize per-consumer state rows.
        Returns the assigned SQLite sequence number.
        """
        created_at = datetime.now(timezone.utc).isoformat()
        conn = self._open(self._db_path)
        try:
            return _events.save(
                conn, event_id, event_type, source, timestamp, data, routing,
                lambda c, eid, seq, rt: _events.materialize_event_state(
                    c, eid, seq, rt, self.is_event_relevant_internal),
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def is_event_relevant_internal(
        routing: dict[str, Any] | None,
        consumer_id: str,
        consumer_topics: set[str],
    ) -> bool:
        """Internal relevance check used during materialization."""
        return _events.is_event_relevant_internal(routing, consumer_id, consumer_topics)

    # ─── Query helpers ────────────────────────────────────────────────────────

    def list_pending(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the most recent persistent events, newest first."""
        conn = self._open(self._db_path)
        try:
            return _events.list_pending(conn, limit, _events.row_to_event)
        finally:
            conn.close()

    def list_relevant_events(
        self,
        consumer_id: str,
        after_sequence: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Return persistent events relevant to a consumer, ordered by sequence ascending.
        Uses materialized consumer_event_state for relevance filtering.
        """
        conn = self._open(self._db_path)
        try:
            return _events.list_relevant_events(
                conn, consumer_id, after_sequence, limit, MAX_REPLAY_LIMIT,
                _events.row_to_event,
            )
        finally:
            conn.close()

    def count(self) -> int:
        conn = self._open(self._db_path)
        try:
            return _events.count(conn)
        finally:
            conn.close()

    # ─── Consumer registry ────────────────────────────────────────────────────

    def register_consumer(self, consumer_id: str) -> None:
        """
        Idempotently register a consumer.
        Also creates an initial checkpoint at sequence 0.
        """
        conn = self._open(self._db_path)
        try:
            _consumers.register_consumer(conn, consumer_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_consumers(self) -> list[str]:
        conn = self._open(self._db_path)
        try:
            return _consumers.list_consumers(conn)
        finally:
            conn.close()

    def add_topic(self, consumer_id: str, topic: str) -> None:
        conn = self._open(self._db_path)
        try:
            _consumers.add_topic(conn, consumer_id, topic)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_consumer_topics(self, consumer_id: str) -> set[str]:
        conn = self._open(self._db_path)
        try:
            return _consumers.get_consumer_topics(conn, consumer_id)
        finally:
            conn.close()

    # ─── Per-consumer event state ─────────────────────────────────────────────

    def mark_delivered(self, consumer_id: str, event_id: str) -> None:
        """
        Mark an event as delivered to a consumer. Preserves first delivery time.
        """
        conn = self._open(self._db_path)
        try:
            _delivery.mark_delivered(conn, consumer_id, event_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def acknowledge_event(self, consumer_id: str, event_id: str) -> bool:
        """
        Acknowledge an event for a consumer. Idempotent — preserves first ack time.
        Returns True if the event was acknowledged (or was already acknowledged).
        Raises ConsumerNotFoundError if the consumer doesn't exist,
        EventNotFoundError if the event doesn't exist, or EventNotRelevantError
        if the event is not relevant to the consumer.
        """
        conn = self._open(self._db_path)
        try:
            return _delivery.acknowledge_event(conn, consumer_id, event_id)
        except (
            ValueError,
            sqlite3.Error,
            ConsumerNotFoundError,
            EventNotFoundError,
            EventNotRelevantError,
        ):
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_delivered_event_ids(self, consumer_id: str) -> set[str]:
        conn = self._open(self._db_path)
        try:
            return _delivery.get_delivered_event_ids(conn, consumer_id)
        finally:
            conn.close()

    # ─── Checkpoint management ────────────────────────────────────────────────

    def get_checkpoint(self, consumer_id: str) -> int:
        """Return the consumer's current checkpoint sequence (0 if registered)."""
        conn = self._open(self._db_path)
        try:
            return _replay.get_checkpoint(conn, consumer_id)
        except (ValueError, ConsumerNotFoundError):
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_checkpoint_info(self, consumer_id: str) -> dict[str, Any]:
        """Return the consumer's durable checkpoint position and last update.

        Returns ``{"checkpoint": int, "updated_at": str}`` where ``updated_at``
        is the persisted ISO-8601 timestamp of the last checkpoint write.
        Raises ConsumerNotFoundError if the consumer has no checkpoint row.
        """
        conn = self._open(self._db_path)
        try:
            checkpoint, updated_at = _replay.get_checkpoint_info(conn, consumer_id)
            return {"checkpoint": checkpoint, "updated_at": updated_at}
        except (ValueError, ConsumerNotFoundError):
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_consumer_inbox_status(self, consumer_id: str) -> dict[str, Any]:
        """Return compact inbox status for a consumer.

        Returns ``{"consumer_id", "checkpoint", "pending_count",
        "latest_sequence"}`` where ``pending_count`` is the number of
        unacknowledged relevant events after the checkpoint and
        ``latest_sequence`` is the highest sequence among them (None when
        there are none). This is a wake-up/status resource — it never returns
        the replay backlog itself.

        Raises ConsumerNotFoundError if the consumer is not registered.
        """
        conn = self._open(self._db_path)
        try:
            return _replay.get_consumer_inbox_status(conn, consumer_id)
        except (ValueError, ConsumerNotFoundError):
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def advance_checkpoint(self, consumer_id: str) -> int:
        """
        Advance the consumer's checkpoint to the highest safe sequence.

        Safe sequence = the highest sequence N such that there is no relevant
        unacknowledged persistent event with sequence <= N.

        Irrelevant events (not in consumer_event_state for this consumer) are
        skipped — they don't block checkpoint advancement.

        Algorithm:
          1. Find the first unacknowledged relevant event AFTER current checkpoint.
          2. If found at sequence N, candidate = N - 1.
          3. If not found, candidate = max(sequence) for this consumer.
          4. new_checkpoint = MAX(current, candidate) — monotonic guard.
        """
        conn = self._open(self._db_path)
        try:
            return _replay.advance_checkpoint(conn, consumer_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def replay_events(
        self,
        consumer_id: str,
        limit: int = 50,
        after_sequence: int | None = None,
    ) -> dict[str, Any]:
        """
        Replay events for a consumer starting from their durable checkpoint.

        Returns events that are:
        - Relevant to the consumer (via materialized consumer_event_state)
        - After the consumer's checkpoint (or after_sequence if provided)
        - Not yet acknowledged
        - Ordered by sequence ASC
        """
        conn = self._open(self._db_path)
        try:
            result = _replay.replay_events(
                conn, consumer_id, limit, MAX_REPLAY_LIMIT,
                _events.row_to_event, after_sequence,
            )
            return result
        except (ValueError, ConsumerNotFoundError):
            raise
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ─── Routing decision ─────────────────────────────────────────────────────

    @staticmethod
    def is_event_relevant(
        event: dict[str, Any],
        consumer_id: str,
        consumer_topics: set[str],
    ) -> bool:
        """
        Determine whether an event is relevant to a consumer.
        Used for non-materialized queries (e.g. list_relevant_events with custom filters).
        """
        return _events.is_event_relevant(
            event.get("routing"), consumer_id, consumer_topics)

    def list_relevant_consumers(
        self, routing: dict[str, Any] | None
    ) -> list[str]:
        """
        Return the registered consumers for whom ``routing`` makes an event
        relevant.

        Mirrors the durable materialization predicate exactly (same consumer
        set, same topic map, same relevance check), so the live notification
        set always matches the durable materialized set. Used post-persistence
        to decide which consumer inboxes receive a live wake-up notification.
        """
        conn = self._open(self._db_path)
        try:
            return _events.list_relevant_consumers(
                conn, routing, self.is_event_relevant_internal)
        finally:
            conn.close()

    # ─── Alert definitions (generic alert engine) ────────────────────────────

    def create_alert(
        self,
        alert_id: str,
        consumer_id: str,
        name: str | None,
        source: str,
        event_type: str | None,
        field_path: str,
        operator: str,
        value: Any,
        one_shot: bool,
    ) -> dict[str, Any]:
        """Persist a new alert definition and return it. Opens/commits its own txn."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._open(self._db_path)
        try:
            _alerts.insert_alert(
                conn, alert_id, consumer_id, name, source, event_type,
                field_path, operator, json.dumps(value, ensure_ascii=False, allow_nan=False), one_shot, now,
            )
            return _alerts.get_alert(conn, consumer_id, alert_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_alerts(
        self, consumer_id: str, enabled: bool | None = None
    ) -> list[dict[str, Any]]:
        """List alerts owned by a consumer (optional enabled filter)."""
        conn = self._open(self._db_path)
        try:
            return _alerts.list_alerts_by_consumer(conn, consumer_id, enabled)
        finally:
            conn.close()

    def list_alerts_by_source_enabled(self, source: str) -> list[dict[str, Any]]:
        """Evaluation candidate query: enabled alerts for a source."""
        conn = self._open(self._db_path)
        try:
            return _alerts.list_alerts_by_source_enabled(conn, source)
        finally:
            conn.close()

    def get_alert(self, consumer_id: str, alert_id: str) -> dict[str, Any] | None:
        """Return a single alert, ownership-checked. None if not found."""
        conn = self._open(self._db_path)
        try:
            return _alerts.get_alert(conn, consumer_id, alert_id)
        finally:
            conn.close()

    def enable_alert(self, consumer_id: str, alert_id: str) -> bool:
        """Enable an alert. Returns True only if state actually changed."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._open(self._db_path)
        try:
            return _alerts.update_alert_enabled(conn, consumer_id, alert_id, True, now)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def disable_alert(self, consumer_id: str, alert_id: str) -> bool:
        """Disable an alert. Returns True only if state actually changed."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._open(self._db_path)
        try:
            return _alerts.update_alert_enabled(conn, consumer_id, alert_id, False, now)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def record_alert_trigger(self, alert_id: str) -> None:
        """Atomically record a successful alert trigger (state update)."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._open(self._db_path)
        try:
            _alerts.record_alert_trigger(conn, alert_id, now)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ─── Source state (durable cursors) ──────────────────────────────────────

    def get_source_state(self, source_name: str, key: str) -> str | None:
        """Read a single source-state value. Returns None if not set."""
        conn = self._open(self._db_path)
        try:
            return _source_state.get_source_state(conn, source_name, key)
        finally:
            conn.close()

    def set_source_state(self, source_name: str, key: str, value: str) -> None:
        """Write a source-state value (upsert)."""
        conn = self._open(self._db_path)
        try:
            _source_state.set_source_state(conn, source_name, key, value)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_all_source_state(self, source_name: str) -> dict[str, str]:
        """Read all key-value pairs for a source."""
        conn = self._open(self._db_path)
        try:
            return _source_state.get_all_source_state(conn, source_name)
        finally:
            conn.close()

    @property
    def db_path(self) -> str:
        return self._db_path

    # ─── Encrypted secrets (v10; values are ciphertext, never plaintext) ──────

    def upsert_secrets(self, provider: str,
                       items: dict[str, tuple[str, str]]) -> None:
        """Transactionally upsert secret rows (name -> (ciphertext, scheme)).

        The caller (CredentialStore) encrypts; this layer stores opaque
        strings and never sees plaintext.
        """
        conn = self._open(self._db_path)
        try:
            _secrets.upsert_secrets(conn, provider, items)
        finally:
            conn.close()

    def get_secret(self, provider: str, name: str) -> tuple[str, str] | None:
        """Return (encrypted_value, encryption_scheme) or None."""
        conn = self._open(self._db_path)
        try:
            return _secrets.get_secret(conn, provider, name)
        finally:
            conn.close()

    def has_secret(self, provider: str, name: str) -> bool:
        conn = self._open(self._db_path)
        try:
            return _secrets.has_secret(conn, provider, name)
        finally:
            conn.close()

    def has_any_secret(self) -> bool:
        """True when any encrypted secret exists for any provider."""
        conn = self._open(self._db_path)
        try:
            return _secrets.has_any_secret(conn)
        finally:
            conn.close()

    def iter_all_secrets(self):
        """Yield (provider, name, encrypted_value) for every stored secret."""
        conn = self._open(self._db_path)
        try:
            yield from _secrets.iter_all_secrets(conn)
        finally:
            conn.close()

    def schema_version(self) -> int:
        """Current on-disk schema version (PRAGMA user_version)."""
        conn = self._open(self._db_path)
        try:
            return get_schema_version(conn)
        finally:
            conn.close()

    def delete_provider_secrets(self, provider: str) -> int:
        conn = self._open(self._db_path)
        try:
            return _secrets.delete_provider_secrets(conn, provider)
        finally:
            conn.close()

    # ─── Product tables: instruments / watchlists / alerts (v11) ──────────────

    def replace_provider_instruments(self, provider: str,
                                     records: list[dict[str, Any]]) -> int:
        conn = self._open(self._db_path)
        try:
            return _products.replace_provider_instruments(conn, provider,
                                                          records)
        finally:
            conn.close()

    def search_instruments(self, **kw: Any) -> list[dict[str, Any]]:
        conn = self._open(self._db_path)
        try:
            return _products.search_instruments(conn, **kw)
        finally:
            conn.close()

    def derivative_expiries(self, underlying: str,
                            instrument_type: str) -> list[str]:
        """Sorted distinct expiries for one underlying's futures/options."""
        conn = self._open(self._db_path)
        try:
            return _products.derivative_expiries(
                conn, underlying=underlying, instrument_type=instrument_type)
        finally:
            conn.close()

    def option_strikes(self, underlying: str, expiry: str) -> list[dict]:
        """All option contracts for one underlying+expiry, strike-sorted."""
        conn = self._open(self._db_path)
        try:
            return _products.option_strikes(
                conn, underlying=underlying, expiry=expiry)
        finally:
            conn.close()

    def get_instrument(self, provider: str,
                       instrument_token: str) -> dict[str, Any] | None:
        conn = self._open(self._db_path)
        try:
            return _products.get_instrument(conn, provider, instrument_token)
        finally:
            conn.close()

    def instruments_sync_state(self) -> list[dict[str, Any]]:
        conn = self._open(self._db_path)
        try:
            return _products.instruments_sync_state(conn)
        finally:
            conn.close()

    def list_all_instruments(self) -> list[dict[str, Any]]:
        """All catalog rows (used to populate the B2 identity resolver)."""
        conn = self._open(self._db_path)
        try:
            return _products.list_all_instruments(conn)
        finally:
            conn.close()

    def list_watchlists(self) -> list[dict[str, Any]]:
        conn = self._open(self._db_path)
        try:
            return _products.list_watchlists(conn)
        finally:
            conn.close()

    def create_watchlist(self, name: str) -> dict[str, Any]:
        conn = self._open(self._db_path)
        try:
            return _products.create_watchlist(conn, name)
        finally:
            conn.close()

    def rename_watchlist(self, wl_id: int, name: str) -> bool:
        conn = self._open(self._db_path)
        try:
            return _products.rename_watchlist(conn, wl_id, name)
        finally:
            conn.close()

    def delete_watchlist(self, wl_id: int) -> bool:
        conn = self._open(self._db_path)
        try:
            return _products.delete_watchlist(conn, wl_id)
        finally:
            conn.close()

    def list_watchlist_items(self, wl_id: int) -> list[dict[str, Any]]:
        conn = self._open(self._db_path)
        try:
            return _products.list_watchlist_items(conn, wl_id)
        finally:
            conn.close()

    def add_watchlist_item(self, wl_id: int, **kw: Any) -> dict | None:
        conn = self._open(self._db_path)
        try:
            return _products.add_watchlist_item(conn, wl_id, **kw)
        finally:
            conn.close()

    def remove_watchlist_item(self, item_id: int) -> bool:
        conn = self._open(self._db_path)
        try:
            return _products.remove_watchlist_item(conn, item_id)
        finally:
            conn.close()

    def reorder_watchlist_items(self, wl_id: int,
                                item_ids: list[int]) -> bool:
        conn = self._open(self._db_path)
        try:
            return _products.reorder_watchlist_items(conn, wl_id, item_ids)
        finally:
            conn.close()

    def create_market_alert(self, **kw: Any) -> dict[str, Any]:
        """Market/product alert facade (market_alerts table).

        Distinct name from the generic create_alert so the two alert
        families never collide on the EventStore facade.
        """
        conn = self._open(self._db_path)
        try:
            return _products.create_alert(conn, **kw)
        finally:
            conn.close()

    def list_market_alerts(self) -> list[dict[str, Any]]:
        """Market/product alert facade (market_alerts table).

        Distinct name from the generic list_alerts so the two alert
        families never collide on the EventStore facade.
        """
        conn = self._open(self._db_path)
        try:
            return _products.list_alerts(conn)
        finally:
            conn.close()

    def delete_alert(self, alert_id: int) -> bool:
        conn = self._open(self._db_path)
        try:
            return _products.delete_alert(conn, alert_id)
        finally:
            conn.close()

    def set_alert_enabled(self, alert_id: int, enabled: bool) -> bool:
        conn = self._open(self._db_path)
        try:
            return _products.set_alert_enabled(conn, alert_id, enabled)
        finally:
            conn.close()

    def rearm_alert(self, alert_id: int) -> bool:
        conn = self._open(self._db_path)
        try:
            return _products.rearm_alert(conn, alert_id)
        finally:
            conn.close()

    def record_trigger(self, alert_id: int) -> None:
        conn = self._open(self._db_path)
        try:
            _products.record_trigger(conn, alert_id)
        finally:
            conn.close()

    def load_enabled_alerts(self) -> list[dict[str, Any]]:
        conn = self._open(self._db_path)
        try:
            return _products.load_enabled_alerts(conn)
        finally:
            conn.close()

    # ─── Condition alerts (advanced market_condition, schema v13) ────────────
    # Internal APIs only — NOT public MCP tools in B2. The 42-tool surface
    # and CONTRACT_VERSION are frozen.

    def create_condition_alert(
        self, *, consumer_id: str, name: str | None = None,
        trigger_mode: str = "repeat",
        condition_json: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Create a condition alert; returns the new alert_id (UUID hex)."""
        conn = self._open(self._db_path)
        try:
            return _condition_alerts.create_condition_alert(
                conn, consumer_id=consumer_id, name=name,
                trigger_mode=trigger_mode, condition_json=condition_json,
                metadata=metadata)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_condition_alerts(
        self, consumer_id: str | None = None
    ) -> list[dict[str, Any]]:
        conn = self._open(self._db_path)
        try:
            return _condition_alerts.list_condition_alerts(conn, consumer_id)
        finally:
            conn.close()

    def get_condition_alert(self, alert_id: str) -> dict[str, Any] | None:
        conn = self._open(self._db_path)
        try:
            return _condition_alerts.get_condition_alert(conn, alert_id)
        finally:
            conn.close()

    def set_condition_alert_enabled(self, alert_id: str, enabled: bool) -> None:
        conn = self._open(self._db_path)
        try:
            _condition_alerts.set_condition_alert_enabled(
                conn, alert_id, enabled)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def delete_condition_alert(self, alert_id: str) -> None:
        conn = self._open(self._db_path)
        try:
            _condition_alerts.delete_condition_alert(conn, alert_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def reset_condition_runtime_state(self, alert_id: str) -> None:
        """Delete runtime-state rows for an alert (re-arm on enable)."""
        conn = self._open(self._db_path)
        try:
            _condition_alerts.reset_condition_runtime_state(conn, alert_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def load_enabled_condition_alerts(self) -> list[dict[str, Any]]:
        conn = self._open(self._db_path)
        try:
            return _condition_alerts.load_enabled_condition_alerts(conn)
        finally:
            conn.close()

    def load_condition_runtime_state(
        self) -> dict[str, dict[str, dict[str, str]]]:
        """Return runtime state keyed by alert_id → condition_id → state."""
        conn = self._open(self._db_path)
        try:
            return _condition_alerts.load_condition_runtime_state(conn)
        finally:
            conn.close()

    def save_condition_runtime_states(
        self,
        *,
        alert_id: str,
        states: dict[str, dict[str, str]],
    ) -> None:
        """Batch upsert runtime state for multiple nodes of one alert."""
        conn = self._open(self._db_path)
        try:
            _condition_alerts.save_condition_runtime_states(
                conn, alert_id=alert_id, states=states,
                updated_at=_condition_alerts._now())
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_condition_runtime_state(
        self, *, alert_id: str, condition_id: str,
        last_result: str, crossing_side: str,
    ) -> None:
        """Upsert runtime state for one condition (standalone state write)."""
        conn = self._open(self._db_path)
        try:
            _condition_alerts.save_condition_runtime_state(
                conn, alert_id=alert_id, condition_id=condition_id,
                last_result=last_result, crossing_side=crossing_side,
                updated_at=datetime.now(timezone.utc).isoformat())
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_condition_trigger(
        self,
        *,
        alert_id: str,
        consumer_id: str,
        event_id: str,
        event_type: str,
        source: str,
        timestamp: str,
        data: dict[str, Any],
        routing: dict[str, Any] | None,
        enabled: bool,
        trigger_count: int,
        last_triggered_at: str,
        state_updates: dict[str, dict[str, str]],
    ) -> int:
        """Atomically persist a condition trigger in ONE transaction.

        Runtime state (batch) + alert row + persistent ``alert.triggered``
        event + consumer materialization commit together; any failure rolls
        back everything (a lost trigger is forbidden). Returns the event
        sequence.
        """
        conn = self._open(self._db_path)
        try:
            return _condition_alerts.save_condition_trigger(
                conn,
                alert_id=alert_id,
                consumer_id=consumer_id,
                event_id=event_id,
                event_type=event_type,
                source=source,
                timestamp=timestamp,
                data=data,
                routing=routing,
                enabled=enabled,
                trigger_count=trigger_count,
                last_triggered_at=last_triggered_at,
                state_updates=state_updates,
                materialize_fn=lambda c, eid, seq, rt: _events.materialize_event_state(
                    c, eid, seq, rt, self.is_event_relevant_internal),
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ─── Alert trigger history (durable, restart-safe audit log) ────────────

    def record_alert_trigger_history(
        self, alert_id: int, exchange: str | None,
        instrument_token: str | None, tradingsymbol: str | None,
        field: str | None, operator: str | None, threshold: float | None,
        observed_value: float | None, provider: str | None,
        triggered_at: str | None = None,
    ) -> int:
        """Persist one alert firing with full context. Opens/commits own txn."""
        if triggered_at is None:
            triggered_at = datetime.now(timezone.utc).isoformat()
        conn = self._open(self._db_path)
        try:
            return _products.insert_alert_trigger_history(
                conn, alert_id=alert_id, exchange=exchange,
                instrument_token=instrument_token,
                tradingsymbol=tradingsymbol, field=field, operator=operator,
                threshold=threshold, observed_value=observed_value,
                provider=provider, triggered_at=triggered_at)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_alert_trigger_history(
        self, alert_id: int | None = None, limit: int = 50,
        offset: int = 0, provider: str | None = None,
    ) -> list[dict[str, Any]]:
        """Bounded, paginated history query (newest first).

        limit is clamped to [1, 500]; offset must be >= 0. Invalid values
        fall back to safe defaults rather than raising (the API layer does
        strict validation and returns 400 before calling this).
        """
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(500, limit))
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        conn = self._open(self._db_path)
        try:
            return _products.list_alert_trigger_history(
                conn, alert_id=alert_id, limit=limit, offset=offset,
                provider=provider)
        finally:
            conn.close()

    def count_alert_trigger_history(
        self, alert_id: int | None = None, provider: str | None = None,
    ) -> int:
        conn = self._open(self._db_path)
        try:
            return _products.count_alert_trigger_history(
                conn, alert_id=alert_id, provider=provider)
        finally:
            conn.close()

    def clear_alert_trigger_history(self, alert_id: int | None = None) -> int:
        """Delete history. If alert_id given, only that alert's rows;
        otherwise ALL history. Returns number of rows deleted."""
        conn = self._open(self._db_path)
        try:
            return _products.clear_alert_trigger_history(conn, alert_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()



    # ─── Source deduplication (durable, restart-safe) ─────────────────────────

    def source_item_seen(self, source_name: str, external_id: str) -> bool:
        """
        Return True if (source_name, external_id) was already marked as seen.

        Used by sources for durable, restart-safe deduplication. The key is the
        composite (source_name, external_id) so two different sources may each
        track the same external ID independently.
        """
        conn = self._open(self._db_path)
        try:
            return _source_state.source_item_seen(conn, source_name, external_id)
        finally:
            conn.close()

    def mark_source_item_seen(
        self, source_name: str, external_id: str, seen_at: str
    ) -> None:
        """Record an external ID as seen (idempotent upsert)."""
        conn = self._open(self._db_path)
        try:
            _source_state.mark_source_item_seen(conn, source_name, external_id, seen_at)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def prune_source_seen_items(self, source_name: str, max_items: int) -> int:
        """
        Delete the oldest seen IDs for a source when over the configured limit.

        Keeps the most recent ``max_items`` rows (ordered by seen_at, then rowid).
        Returns the number of rows deleted. No-op when already at/under the limit.
        """
        conn = self._open(self._db_path)
        try:
            return _source_state.prune_source_seen_items(conn, source_name, max_items)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def prune(self, max_age_days: float = 0, max_rows: int = 0) -> dict[str, Any]:
        """
        Consumer-safe retention prune of persistent_events.

        Retention limits define what MAY be pruned; consumer replay safety
        defines what is SAFE: events still required by any registered consumer
        (relevant, unacknowledged, above their replay floor) are preserved.
        Dependent consumer_event_state rows for actually-deleted events are
        removed in the same transaction. Both limits 0 → no-op.
        """
        conn = self._open(self._db_path)
        try:
            return _retention.prune_persistent_events(conn, max_age_days, max_rows)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ─── Recent observational history ──────────────────────────────────────────

    def append_recent_event(self, event: dict[str, Any], capacity: int) -> None:
        """Append a published event to the durable recent journal.

        Capacity is supplied by the caller (events.RECENT_HISTORY_CAPACITY).
        Failure isolation: the caller is responsible for catching exceptions so
        an observational-journal failure never fails the authoritative event.
        """
        conn = self._open(self._db_path)
        try:
            _recent_events.append_recent_event(conn, event, capacity)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_recent_events(
        self, limit: int, newest_first: bool = False
    ) -> list[dict[str, Any]]:
        """Return reconstructed recent events (observational journal)."""
        conn = self._open(self._db_path)
        try:
            return _recent_events.list_recent_events(conn, limit, newest_first)
        finally:
            conn.close()

    # ─── System metric aggregates (cheap global queries) ───────────────────────

    def persistent_event_count(self) -> int:
        """Return COUNT(*) from persistent_events."""
        conn = self._open(self._db_path)
        try:
            return _events.count(conn)
        finally:
            conn.close()

    def persistent_high_water(self) -> int:
        """Return MAX(sequence) from persistent_events, or 0 if empty."""
        conn = self._open(self._db_path)
        try:
            row = conn.execute(
                "SELECT MAX(sequence) FROM persistent_events"
            ).fetchone()
            return row[0] if row and row[0] is not None else 0
        finally:
            conn.close()

    def count_consumers(self) -> int:
        """Return COUNT(*) from consumers table."""
        conn = self._open(self._db_path)
        try:
            row = conn.execute("SELECT COUNT(*) FROM consumers").fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def count_unacked_deliveries(self) -> int:
        """Return COUNT(*) of unacknowledged consumer_event_state rows."""
        conn = self._open(self._db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM consumer_event_state "
                "WHERE acknowledged_at IS NULL"
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def option_underlyings(self, q: str | None = None,
                           limit: int = 25) -> list[dict[str, Any]]:
        conn = self._open(self._db_path)
        try:
            return _products.option_underlyings(conn, q=q, limit=limit)
        finally:
            conn.close()

    def option_expiries(self, underlying: str) -> list[str]:
        conn = self._open(self._db_path)
        try:
            return _products.option_expiries(conn, underlying)
        finally:
            conn.close()

    def backup_to(self, dest_path: str) -> None:
        """Consistent online backup via the SQLite backup API.

        The backup contains encrypted secrets (ciphertext) only; the
        master key (data/master.key) is required to decrypt them and is
        NOT included.
        """
        import os
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        src = self._open(self._db_path)
        try:
            dst = sqlite3.connect(dest_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            conn_close = getattr(src, "close", None)
            if conn_close:
                conn_close()

    # ─── News & Sentiment (N1) ──────────────────────────────────────────────

    def list_news_sources(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        conn = self._open(self._db_path)
        try:
            return _news.list_sources(conn, enabled_only=enabled_only)
        finally:
            conn.close()

    def get_news_source(self, source_id: str) -> dict[str, Any] | None:
        conn = self._open(self._db_path)
        try:
            return _news.get_source(conn, source_id)
        finally:
            conn.close()

    def upsert_news_source(self, *, source_id: str, name: str,
                           source_type: str, category: str, enabled: bool,
                           config_json: dict[str, Any] | None = None) -> None:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        conn = self._open(self._db_path)
        try:
            _news.upsert_source(conn, source_id=source_id, name=name,
                                source_type=source_type, category=category,
                                enabled=enabled, config_json=config_json,
                                now_iso=now_iso)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def delete_news_source(self, source_id: str) -> bool:
        conn = self._open(self._db_path)
        try:
            result = _news.delete_source(conn, source_id)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def set_news_source_enabled(self, source_id: str, enabled: bool) -> bool:
        conn = self._open(self._db_path)
        try:
            result = _news.set_source_enabled(conn, source_id, enabled)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_news_source_tombstones(self) -> list[str]:
        """Return ids of user-deleted sources (never re-seed these)."""
        conn = self._open(self._db_path)
        try:
            return _news.list_tombstones(conn)
        finally:
            conn.close()

    def is_news_article_seen(self, article_id: str) -> bool:
        conn = self._open(self._db_path)
        try:
            return _news.is_article_seen(conn, article_id)
        finally:
            conn.close()

    def store_news_articles(self, articles: list[dict[str, Any]],
                            fetched_at: str) -> int:
        conn = self._open(self._db_path)
        try:
            count = _news.store_articles_batch(conn, articles, fetched_at)
            conn.commit()
            return count
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def recent_news_articles(self, *, source_id: str | None = None,
                             limit: int = 50) -> list[dict[str, Any]]:
        conn = self._open(self._db_path)
        try:
            return _news.recent_articles(conn, source_id=source_id, limit=limit)
        finally:
            conn.close()

    def prune_news_articles(self, max_age_days: int) -> int:
        conn = self._open(self._db_path)
        try:
            count = _news.prune_old_articles(conn, max_age_days)
            conn.commit()
            return count
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
