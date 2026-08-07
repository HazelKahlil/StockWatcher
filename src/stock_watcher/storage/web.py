"""Schema v7 web repositories: users, sessions, user state and audit log.

Pure SQL over SQLiteStore connections; the services layer owns transaction
boundaries. No Qt, no macOS, no keyring imports.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from stock_watcher.domain import SHANGHAI
from stock_watcher.storage import SQLiteStore

SESSION_TOKEN_BYTES = 32
SESSION_CSRF_BYTES = 32
SESSION_RETENTION_DAYS = 30


def _now() -> datetime:
    return datetime.now(SHANGHAI)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_opaque_token() -> str:
    """Return a URL-safe opaque token (session or CSRF value)."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)


class UserRepository:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    @staticmethod
    def _row(row: tuple[Any, ...]) -> dict[str, Any]:
        keys = (
            "user_id",
            "username",
            "password_hash",
            "role",
            "active",
            "created_at",
            "updated_at",
            "last_login_at",
            "password_changed_at",
            "created_by",
        )
        return dict(zip(keys, row))

    def create(
        self,
        *,
        username: str,
        password_hash: str,
        role: str,
        created_by: int | None = None,
    ) -> dict[str, Any]:
        if role not in {"tester", "admin"}:
            raise ValueError("role must be tester or admin")
        now = _now().isoformat()
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO web_users "
                "(username, password_hash, role, active, created_at, updated_at, "
                "last_login_at, password_changed_at, created_by) "
                "VALUES (?, ?, ?, 1, ?, ?, NULL, ?, ?)",
                (username, password_hash, role, now, now, now, created_by),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("user insert did not return an id")
            user_id = int(cursor.lastrowid)
        return self.get_by_id(user_id)  # type: ignore[return-value]

    def get_by_username(self, username: str) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT user_id, username, password_hash, role, active, created_at, "
                "updated_at, last_login_at, password_changed_at, created_by "
                "FROM web_users WHERE username = ? COLLATE NOCASE",
                (username,),
            ).fetchone()
        return None if row is None else self._row(row)

    def get_by_id(self, user_id: int) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT user_id, username, password_hash, role, active, created_at, "
                "updated_at, last_login_at, password_changed_at, created_by "
                "FROM web_users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return None if row is None else self._row(row)

    def list_users(self) -> list[dict[str, Any]]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT user_id, username, password_hash, role, active, created_at, "
                "updated_at, last_login_at, password_changed_at, created_by "
                "FROM web_users ORDER BY user_id"
            ).fetchall()
        return [self._row(row) for row in rows]

    def update(
        self,
        user_id: int,
        *,
        role: str | None = None,
        active: bool | None = None,
        password_hash: str | None = None,
        last_login_at: str | None = None,
    ) -> dict[str, Any] | None:
        updates: list[str] = []
        values: list[Any] = []
        if role is not None:
            if role not in {"tester", "admin"}:
                raise ValueError("role must be tester or admin")
            updates.append("role = ?")
            values.append(role)
        if active is not None:
            updates.append("active = ?")
            values.append(int(active))
        if password_hash is not None:
            updates.append("password_hash = ?")
            values.append(password_hash)
            updates.append("password_changed_at = ?")
            values.append(_now().isoformat())
        if last_login_at is not None:
            updates.append("last_login_at = ?")
            values.append(last_login_at)
        if not updates:
            return self.get_by_id(user_id)
        updates.append("updated_at = ?")
        values.append(_now().isoformat())
        values.append(user_id)
        with self.store.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE web_users SET {', '.join(updates)} WHERE user_id = ?",
                values,
            )
            if cursor.rowcount != 1:
                return None
        return self.get_by_id(user_id)

    def count_active_admins(self) -> int:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM web_users WHERE role = 'admin' AND active = 1"
            ).fetchone()
        return 0 if row is None else int(row[0])


class SessionRepository:
    """Opaque server-side sessions; only hashes are persisted."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def create(
        self,
        *,
        user_id: int,
        token: str,
        csrf_value: str,
        ip_hash: str | None,
        user_agent: str,
        absolute_hours: float = 12.0,
        idle_minutes: float = 120.0,
    ) -> None:
        now = _now()
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO web_sessions "
                "(session_token_hash, user_id, csrf_token_hash, created_at, "
                "last_seen_at, idle_expires_at, absolute_expires_at, revoked_at, "
                "ip_hash, user_agent) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    _sha256(token),
                    user_id,
                    _sha256(csrf_value),
                    now.isoformat(),
                    now.isoformat(),
                    (now + timedelta(minutes=idle_minutes)).isoformat(),
                    (now + timedelta(hours=absolute_hours)).isoformat(),
                    ip_hash,
                    user_agent[:512],
                ),
            )

    def get(self, token: str) -> dict[str, Any] | None:
        token_hash = _sha256(token)
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT s.session_token_hash, s.user_id, s.csrf_token_hash, "
                "s.created_at, s.last_seen_at, s.idle_expires_at, "
                "s.absolute_expires_at, s.revoked_at, s.ip_hash, s.user_agent, "
                "u.username, u.role, u.active "
                "FROM web_sessions s JOIN web_users u ON u.user_id = s.user_id "
                "WHERE s.session_token_hash = ?",
                (token_hash,),
            ).fetchone()
        if row is None:
            return None
        keys = (
            "session_token_hash",
            "user_id",
            "csrf_token_hash",
            "created_at",
            "last_seen_at",
            "idle_expires_at",
            "absolute_expires_at",
            "revoked_at",
            "ip_hash",
            "user_agent",
            "username",
            "role",
            "active",
        )
        return dict(zip(keys, row))

    def csrf_hash_for(self, token: str) -> str | None:
        session = self.get(token)
        return None if session is None else str(session["csrf_token_hash"])

    def touch(self, token: str, *, idle_minutes: float = 120.0) -> bool:
        now = _now()
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE web_sessions SET last_seen_at = ?, idle_expires_at = ? "
                "WHERE session_token_hash = ? AND revoked_at IS NULL",
                (
                    now.isoformat(),
                    (now + timedelta(minutes=idle_minutes)).isoformat(),
                    _sha256(token),
                ),
            )
            return cursor.rowcount == 1

    def revoke(self, token: str) -> bool:
        now = _now().isoformat()
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE web_sessions SET revoked_at = ? "
                "WHERE session_token_hash = ? AND revoked_at IS NULL",
                (now, _sha256(token)),
            )
            return cursor.rowcount == 1

    def revoke_all_for_user(self, user_id: int) -> int:
        now = _now().isoformat()
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "UPDATE web_sessions SET revoked_at = ? "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (now, user_id),
            )
            return max(cursor.rowcount, 0)

    def cleanup_expired(self, *, now: datetime | None = None) -> int:
        """Delete revoked sessions older than the retention window."""
        current = now or _now()
        cutoff = (current - timedelta(days=SESSION_RETENTION_DAYS)).isoformat()
        with self.store.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM web_sessions WHERE revoked_at IS NOT NULL "
                "AND revoked_at < ?",
                (cutoff,),
            )
            return max(cursor.rowcount, 0)


class UserStateRepository:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def ensure(self, user_id: int) -> None:
        now = _now().isoformat()
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO web_user_state "
                "(user_id, last_event_id, browser_notifications_enabled, "
                "sound_enabled, updated_at) VALUES (?, 0, 0, 0, ?)",
                (user_id, now),
            )

    def get(self, user_id: int) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT user_id, last_event_id, browser_notifications_enabled, "
                "sound_enabled, updated_at FROM web_user_state WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "user_id": row[0],
            "last_event_id": row[1],
            "browser_notifications_enabled": row[2],
            "sound_enabled": row[3],
            "updated_at": row[4],
        }

    def update(
        self,
        user_id: int,
        *,
        last_event_id: int | None = None,
        browser_notifications_enabled: bool | None = None,
        sound_enabled: bool | None = None,
    ) -> None:
        self.ensure(user_id)
        updates: list[str] = []
        values: list[Any] = []
        if last_event_id is not None:
            updates.append("last_event_id = ?")
            values.append(int(last_event_id))
        if browser_notifications_enabled is not None:
            updates.append("browser_notifications_enabled = ?")
            values.append(int(browser_notifications_enabled))
        if sound_enabled is not None:
            updates.append("sound_enabled = ?")
            values.append(int(sound_enabled))
        updates.append("updated_at = ?")
        values.append(_now().isoformat())
        values.append(user_id)
        with self.store.transaction() as connection:
            connection.execute(
                f"UPDATE web_user_state SET {', '.join(updates)} WHERE user_id = ?",
                values,
            )


class AuditLogRepository:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def record(
        self,
        *,
        actor_user_id: int | None = None,
        actor_session_hash_prefix: str | None = None,
        action: str,
        object_type: str | None = None,
        object_id: str | None = None,
        outcome: str,
        request_id: str | None = None,
        detail: dict[str, Any] | None = None,
        connection: Any | None = None,
    ) -> None:
        if outcome not in {"succeeded", "failed", "denied"}:
            raise ValueError("audit outcome must be succeeded/failed/denied")

        def _insert(conn: Any) -> None:
            conn.execute(
                "INSERT INTO web_audit_log "
                "(occurred_at, actor_user_id, actor_session_hash_prefix, action, "
                "object_type, object_id, outcome, request_id, detail_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _now().isoformat(),
                    actor_user_id,
                    actor_session_hash_prefix,
                    action,
                    object_type,
                    object_id,
                    outcome,
                    request_id,
                    json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
                ),
            )

        if connection is not None:
            _insert(connection)
            return
        with self.store.transaction() as connection:
            _insert(connection)

    def list(
        self,
        *,
        limit: int = 100,
        cursor: int | None = None,
        action: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        clauses: list[str] = []
        values: list[Any] = []
        if cursor is not None:
            clauses.append("audit_id < ?")
            values.append(int(cursor))
        if action is not None:
            clauses.append("action = ?")
            values.append(action)
        query = (
            "SELECT audit_id, occurred_at, actor_user_id, actor_session_hash_prefix, "
            "action, object_type, object_id, outcome, request_id, detail_json "
            "FROM web_audit_log"
        )
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY audit_id DESC LIMIT ?"
        values.append(limit)
        with self.store.connect() as connection:
            rows = connection.execute(query, tuple(values)).fetchall()
        return [
            {
                "audit_id": row[0],
                "occurred_at": row[1],
                "actor_user_id": row[2],
                "actor_session_hash_prefix": row[3],
                "action": row[4],
                "object_type": row[5],
                "object_id": row[6],
                "outcome": row[7],
                "request_id": row[8],
                "detail": json.loads(row[9]),
            }
            for row in rows
        ]
