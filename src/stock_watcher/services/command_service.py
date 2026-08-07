"""Durable command queue for the Web internal test.

Web creates commands; the unique Worker claims them with compare-and-set and
writes results. ``manual_refresh`` is globally coalesced through a partial
unique index so simultaneous clicks share one command and one scan.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from stock_watcher.domain import SHANGHAI
from stock_watcher.storage import SQLiteStore

COMMAND_TIMEOUT_SECONDS: dict[str, float] = {
    "manual_refresh": 300.0,
    "universe_refresh": 600.0,
    "token_test": 300.0,
    "token_update": 300.0,
    "summary_generate": 3600.0,
}
COMMAND_MAX_ATTEMPTS = 3

_COMMAND_COLUMNS = (
    "command_id",
    "command_type",
    "status",
    "requested_by",
    "requested_at",
    "idempotency_key",
    "payload_json",
    "secret_request_id",
    "claimed_by",
    "fencing_token",
    "started_at",
    "completed_at",
    "expires_at",
    "attempts",
    "result_json",
    "error_code",
    "error_detail",
)


class CommandType(StrEnum):
    MANUAL_REFRESH = "manual_refresh"
    UNIVERSE_REFRESH = "universe_refresh"
    TOKEN_TEST = "token_test"
    TOKEN_UPDATE = "token_update"
    SUMMARY_GENERATE = "summary_generate"


class CommandStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


def _shanghai(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)


class CommandService:
    def __init__(
        self,
        store: SQLiteStore,
        *,
        clock: Callable[[], datetime] | None = None,
        default_timeout_seconds: float | None = None,
    ) -> None:
        self.store = store
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self.default_timeout_seconds = default_timeout_seconds

    # -- creation -------------------------------------------------------

    def create(
        self,
        *,
        command_type: CommandType,
        requested_by: int,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        secret_request_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a queued command; manual_refresh coalesces into the live one."""
        if command_type not in CommandType:
            raise ValueError(f"unknown command type: {command_type}")
        now = _shanghai(self._clock())
        command_id = uuid.uuid4().hex
        if expires_at is None:
            timeout = (
                self.default_timeout_seconds
                or COMMAND_TIMEOUT_SECONDS[command_type.value]
            )
            expires_at = now + timedelta(seconds=timeout)
        with self.store.transaction() as connection:
            try:
                connection.execute(
                    "INSERT INTO web_commands "
                    "(command_id, command_type, status, requested_by, requested_at, "
                    "idempotency_key, payload_json, secret_request_id, claimed_by, "
                    "fencing_token, started_at, completed_at, expires_at, attempts, "
                    "result_json, error_code, error_detail) "
                    "VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, "
                    "?, 0, NULL, NULL, NULL)",
                    (
                        command_id,
                        command_type.value,
                        requested_by,
                        now.isoformat(),
                        idempotency_key,
                        json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                        secret_request_id,
                        expires_at.isoformat(),
                    ),
                )
            except Exception:
                # The partial unique index fired: a manual_refresh (or the
                # caller's idempotency key) is already queued/running.
                existing = self._find_active_like(
                    connection,
                    command_type,
                    idempotency_key,
                )
                if existing is None:
                    raise
                return self._with_coalesced(existing)
        return self.get(command_id)  # type: ignore[return-value]

    def _find_active_like(
        self,
        connection: sqlite3.Connection,
        command_type: CommandType,
        idempotency_key: str | None,
    ) -> dict[str, Any] | None:
        if idempotency_key is not None:
            row = connection.execute(
                "SELECT * FROM web_commands WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is not None:
                return self._row(row)
        row = connection.execute(
            "SELECT * FROM web_commands WHERE command_type = ? "
            "AND status IN ('queued', 'running') "
            "ORDER BY requested_at LIMIT 1",
            (command_type.value,),
        ).fetchone()
        return None if row is None else self._row(row)

    @staticmethod
    def _with_coalesced(command: dict[str, Any]) -> dict[str, Any]:
        output = dict(command)
        output["coalesced"] = True
        return output

    # -- reads ----------------------------------------------------------

    def get(self, command_id: str) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM web_commands WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        if row is None:
            return None
        output = self._row(row)
        output["coalesced"] = False
        return output

    def list_recent(self, *, limit: int = 50) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM web_commands ORDER BY requested_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row(row) for row in rows]

    # -- claiming / completion -----------------------------------------

    def claim_next(
        self,
        *,
        holder_id: str,
        fencing_token: int,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Compare-and-set claim of the oldest queued command."""
        current = _shanghai(now or self._clock())
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM web_commands WHERE status = 'queued' "
                "AND (expires_at IS NULL OR expires_at > ?) "
                "ORDER BY requested_at LIMIT 1",
                (current.isoformat(),),
            ).fetchone()
            if row is None:
                return None
            command = self._row(row)
            cursor = connection.execute(
                "UPDATE web_commands SET status = 'running', claimed_by = ?, "
                "fencing_token = ?, started_at = ?, attempts = attempts + 1 "
                "WHERE command_id = ? AND status = 'queued' "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (
                    holder_id,
                    int(fencing_token),
                    current.isoformat(),
                    command["command_id"],
                    current.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                return None
        command["status"] = CommandStatus.RUNNING.value
        command["claimed_by"] = holder_id
        command["fencing_token"] = fencing_token
        command["started_at"] = current.isoformat()
        command["attempts"] = int(command["attempts"]) + 1
        return command

    def complete(
        self,
        command_id: str,
        *,
        holder_id: str,
        fencing_token: int,
        status: CommandStatus,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        current = _shanghai(now or self._clock())
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE web_commands SET status = ?, completed_at = ?, "
                "result_json = ?, error_code = ?, error_detail = ? "
                "WHERE command_id = ? AND claimed_by = ? AND fencing_token = ? "
                "AND status = 'running'",
                (
                    status.value,
                    current.isoformat(),
                    json.dumps(result or {}, ensure_ascii=False, sort_keys=True),
                    error_code,
                    error_detail,
                    command_id,
                    holder_id,
                    int(fencing_token),
                ),
            )
            return cursor.rowcount == 1

    def cancel(self, command_id: str) -> bool:
        now = _shanghai(self._clock())
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE web_commands SET status = 'cancelled', completed_at = ? "
                "WHERE command_id = ? AND status = 'queued'",
                (now.isoformat(), command_id),
            )
            return cursor.rowcount == 1

    # -- maintenance ----------------------------------------------------

    def expire_stale(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        """Expire queued commands past deadline; retry or fail running ones.

        A worker crash after claiming leaves a running command; once its
        timeout passes it is retried (up to COMMAND_MAX_ATTEMPTS) or failed
        permanently. Returns the transitions as command rows.
        """
        current = _shanghai(now or self._clock())
        transitions: list[dict[str, Any]] = []
        with self.store.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM web_commands WHERE status = 'queued' "
                "AND expires_at IS NOT NULL AND expires_at <= ?",
                (current.isoformat(),),
            ).fetchall()
            for row in rows:
                command = self._row(row)
                connection.execute(
                    "UPDATE web_commands SET status = 'expired', completed_at = ? "
                    "WHERE command_id = ?",
                    (current.isoformat(), command["command_id"]),
                )
                transitions.append(self._row(connection.execute(
                    "SELECT * FROM web_commands WHERE command_id = ?",
                    (command["command_id"],),
                ).fetchone()))
            running = connection.execute(
                "SELECT * FROM web_commands WHERE status = 'running' "
                "AND started_at IS NOT NULL AND expires_at IS NOT NULL "
                "AND expires_at <= ?",
                (current.isoformat(),),
            ).fetchall()
            for row in running:
                command = self._row(row)
                attempts = int(command["attempts"])
                if attempts >= COMMAND_MAX_ATTEMPTS:
                    connection.execute(
                        "UPDATE web_commands SET status = 'failed', completed_at = ?, "
                        "error_code = 'timeout', error_detail = 'max attempts reached' "
                        "WHERE command_id = ?",
                        (current.isoformat(), command["command_id"]),
                    )
                else:
                    timeout = COMMAND_TIMEOUT_SECONDS.get(
                        str(command["command_type"]), 300.0
                    )
                    connection.execute(
                        "UPDATE web_commands SET status = 'queued', claimed_by = NULL, "
                        "fencing_token = NULL, started_at = NULL, expires_at = ? "
                        "WHERE command_id = ?",
                        (
                            (current + timedelta(seconds=timeout)).isoformat(),
                            command["command_id"],
                        ),
                    )
                updated = connection.execute(
                    "SELECT * FROM web_commands WHERE command_id = ?",
                    (command["command_id"],),
                ).fetchone()
                transitions.append(self._row(updated))
        return transitions

    def _row(self, row: tuple[Any, ...]) -> dict[str, Any]:
        output = dict(zip(_COMMAND_COLUMNS, row))
        for key in ("payload_json", "result_json"):
            if output.get(key) is not None:
                try:
                    output[key.replace("_json", "")] = json.loads(str(output[key]))
                except json.JSONDecodeError:
                    pass
                output.pop(key, None)
        return output
