"""Durable event outbox feeding the WebSocket pump.

Business writes and their ``web_events`` rows are committed in the same
SQLite transaction whenever possible; the Web single event pump reads by
auto-increment id so reconnect with ``after_id`` is lossless within the
retention window.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from stock_watcher.domain import SHANGHAI
from stock_watcher.storage import SQLiteStore

EVENT_RETENTION_DAYS = 30
EVENT_RETENTION_MAX_ROWS = 100_000


def _shanghai(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)


class EventOutbox:
    def __init__(
        self,
        store: SQLiteStore,
        *,
        read_store: SQLiteStore | None = None,
        source_commit: str,
        retention_days: int = EVENT_RETENTION_DAYS,
        retention_max_rows: int = EVENT_RETENTION_MAX_ROWS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.read_store = read_store or store
        self.source_commit = source_commit
        self.retention_days = retention_days
        self.retention_max_rows = retention_max_rows
        self._clock = clock or (lambda: datetime.now(SHANGHAI))

    def append(
        self,
        connection: sqlite3.Connection,
        *,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        source_kind: str | None = None,
        source_id: str | None = None,
        visibility: str = "all",
    ) -> int:
        """Insert one event inside the caller's open transaction."""
        if visibility not in {"all", "tester", "admin"}:
            raise ValueError("event visibility must be all/tester/admin")
        occurred_at = _shanghai(self._clock())
        cursor = connection.execute(
            "INSERT INTO web_events "
            "(event_type, occurred_at, source_commit, correlation_id, source_kind, "
            "source_id, visibility, payload_json, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_type,
                occurred_at.isoformat(),
                self.source_commit,
                correlation_id,
                source_kind,
                source_id,
                visibility,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                (occurred_at + timedelta(days=self.retention_days)).isoformat(),
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("event insert did not return an id")
        return int(cursor.lastrowid)

    def append_own(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        source_kind: str | None = None,
        source_id: str | None = None,
        visibility: str = "all",
    ) -> int:
        """Append an event in its own transaction (web-side notifications)."""
        with self.store.transaction() as connection:
            return self.append(
                connection,
                event_type=event_type,
                payload=payload,
                correlation_id=correlation_id,
                source_kind=source_kind,
                source_id=source_id,
                visibility=visibility,
            )

    def read_since(self, after_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
        """Return events with id > after_id, oldest first, bounded."""
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        with self.read_store.connect() as connection:
            rows = connection.execute(
                "SELECT event_id, event_type, occurred_at, source_commit, "
                "correlation_id, source_kind, source_id, visibility, payload_json "
                "FROM web_events WHERE event_id > ? "
                "ORDER BY event_id LIMIT ?",
                (int(after_id), limit),
            ).fetchall()
        return [self._row(row) for row in rows]

    def latest_id(self) -> int:
        with self.read_store.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(event_id), 0) FROM web_events"
            ).fetchone()
        return 0 if row is None else int(row[0])

    def minimum_available_id(self) -> int:
        """Smallest event id still readable; used for resync detection."""
        with self.read_store.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MIN(event_id), 0) FROM web_events"
            ).fetchone()
        return 0 if row is None else int(row[0])

    def prune(self, *, now: datetime | None = None) -> int:
        """Drop events outside the retention window (days and max rows)."""
        current = _shanghai(now or self._clock())
        cutoff = (current - timedelta(days=self.retention_days)).isoformat()
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM web_events WHERE expires_at < ? OR event_id NOT IN ("
                "SELECT event_id FROM web_events ORDER BY event_id DESC LIMIT ?)",
                (cutoff, self.retention_max_rows),
            )
            return max(cursor.rowcount, 0)

    @staticmethod
    def _row(row: tuple[Any, ...]) -> dict[str, Any]:
        keys = (
            "event_id",
            "event_type",
            "occurred_at",
            "source_commit",
            "correlation_id",
            "source_kind",
            "source_id",
            "visibility",
            "payload_json",
        )
        output = dict(zip(keys, row))
        output["payload"] = json.loads(str(output.pop("payload_json")))
        return output
