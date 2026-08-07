"""Authentication: Argon2id passwords, opaque sessions, CSRF, RBAC, rate limits.

Default deny: every API except login/health requires a live session; every
mutation (POST/PATCH/PUT/DELETE) requires a matching ``X-CSRF-Token``.
"""
from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from stock_watcher.storage import SQLiteStore
from stock_watcher.storage.web import (
    AuditLogRepository,
    SessionRepository,
    UserRepository,
    generate_opaque_token,
)

SESSION_COOKIE_NAME = "sw_session"
AUTH_SCHEME = "argon2id"


class AuthError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    minimum_length: int = 12


@dataclass(slots=True)
class RateLimiter:
    """Sliding-window limiter with exponential backoff per key."""

    max_attempts: int = 5
    window_seconds: float = 300.0
    _events: dict[str, deque[float]] = field(default_factory=dict)
    _blocked_until: dict[str, float] = field(default_factory=dict)

    def check(self, key: str) -> None:
        now = time.monotonic()
        blocked_until = self._blocked_until.get(key, 0.0)
        if now < blocked_until:
            raise AuthError(
                "too many attempts; retry later",
                status_code=429,
            )
        events = self._events.setdefault(key, deque())
        while events and now - events[0] > self.window_seconds:
            events.popleft()
        if len(events) >= self.max_attempts:
            backoff = min(300.0, 5.0 * (2 ** (len(events) - self.max_attempts)))
            self._blocked_until[key] = now + backoff
            raise AuthError("too many attempts; retry later", status_code=429)

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        events = self._events.setdefault(key, deque())
        while events and now - events[0] > self.window_seconds:
            events.popleft()
        events.append(now)

    def retry_after(self, key: str) -> int:
        return max(0, int(self._blocked_until.get(key, 0.0) - time.monotonic()) + 1)


def ip_hash(client_ip: str) -> str:
    return hashlib.sha256(client_ip.encode("utf-8")).hexdigest()[:16]


def csrf_value_matches(provided: str, stored_hash: str) -> bool:
    if not provided:
        return False
    return hashlib.sha256(provided.encode("utf-8")).hexdigest() == stored_hash


class AuthService:
    """Server-side session and user management (single web process)."""

    def __init__(
        self,
        store: SQLiteStore,
        *,
        password_hasher: PasswordHasher | None = None,
        absolute_hours: float = 12.0,
        idle_minutes: float = 120.0,
        login_limiter: RateLimiter | None = None,
        command_limiter: RateLimiter | None = None,
        password_policy: PasswordPolicy = PasswordPolicy(),
    ) -> None:
        self.store = store
        self.users = UserRepository(store)
        self.sessions = SessionRepository(store)
        self.audit = AuditLogRepository(store)
        self.hasher = password_hasher or PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=2,
        )
        self.absolute_hours = absolute_hours
        self.idle_minutes = idle_minutes
        self.login_limiter = login_limiter or RateLimiter(max_attempts=5, window_seconds=300)
        self.command_limiter = command_limiter or RateLimiter(max_attempts=20, window_seconds=60)
        self.policy = password_policy

    # -- passwords -------------------------------------------------------

    def hash_password(self, password: str) -> str:
        if len(password) < self.policy.minimum_length:
            raise AuthError(
                f"password must be at least {self.policy.minimum_length} characters",
                status_code=400,
            )
        return self.hasher.hash(password)

    def verify_password(self, password_hash: str, password: str) -> bool:
        try:
            return self.hasher.verify(password_hash, password)
        except (VerifyMismatchError, InvalidHashError):
            return False

    def create_user(
        self,
        *,
        username: str,
        password: str,
        role: str,
        created_by: int | None = None,
    ) -> dict[str, Any]:
        normalized = username.strip().casefold()
        if not normalized:
            raise AuthError("username must not be empty", status_code=400)
        password_hash = self.hash_password(password)
        try:
            user = self.users.create(
                username=normalized,
                password_hash=password_hash,
                role=role,
                created_by=created_by,
            )
        except Exception as error:
            raise AuthError("username already exists", status_code=409) from error
        self.audit.record(
            actor_user_id=created_by,
            action="user.create",
            object_type="user",
            object_id=str(user["user_id"]),
            outcome="succeeded",
            detail={"username": normalized, "role": role},
        )
        return user

    # -- login/logout ----------------------------------------------------

    def login(
        self,
        *,
        username: str,
        password: str,
        client_ip: str | None = None,
        user_agent: str = "",
    ) -> dict[str, Any]:
        normalized = username.strip().casefold()
        limit_key = f"login:{normalized}:{client_ip or 'unknown'}"
        self.login_limiter.check(limit_key)
        user = self.users.get_by_username(normalized)
        if user is None or not user["active"]:
            self.login_limiter.record_failure(limit_key)
            self.audit.record(
                action="auth.login",
                object_type="user",
                object_id=normalized,
                outcome="denied",
                detail={"reason": "invalid_credentials"},
            )
            raise AuthError("用户名或密码错误", status_code=401)
        if not self.verify_password(str(user["password_hash"]), password):
            self.login_limiter.record_failure(limit_key)
            self.audit.record(
                actor_user_id=user["user_id"],
                action="auth.login",
                object_type="user",
                object_id=str(user["user_id"]),
                outcome="denied",
                detail={"reason": "invalid_credentials"},
            )
            raise AuthError("用户名或密码错误", status_code=401)
        token = generate_opaque_token()
        csrf = generate_opaque_token()
        self.sessions.create(
            user_id=int(user["user_id"]),
            token=token,
            csrf_value=csrf,
            ip_hash=ip_hash(client_ip) if client_ip else None,
            user_agent=user_agent,
            absolute_hours=self.absolute_hours,
            idle_minutes=self.idle_minutes,
        )
        from datetime import datetime as _datetime

        from stock_watcher.domain import SHANGHAI as _SHANGHAI

        self.users.update(
            int(user["user_id"]),
            last_login_at=_datetime.now(_SHANGHAI).isoformat(),
        )
        self.audit.record(
            actor_user_id=user["user_id"],
            action="auth.login",
            object_type="user",
            object_id=str(user["user_id"]),
            outcome="succeeded",
        )
        return {
            "token": token,
            "csrf": csrf,
            "user": self.public_user(user),
        }

    def logout(self, token: str) -> None:
        session = self.sessions.get(token)
        if session is None:
            return
        self.sessions.revoke(token)
        self.audit.record(
            actor_user_id=session["user_id"],
            action="auth.logout",
            object_type="session",
            outcome="succeeded",
        )

    def authenticate(self, token: str | None) -> dict[str, Any] | None:
        """Validate a session token; returns the joined session+user row."""
        if not token:
            return None
        session = self.sessions.get(token)
        if session is None:
            return None
        import datetime as _dt

        now = _dt.datetime.now(_dt.UTC)
        for key, attr in (
            ("idle_expires_at", "idle"),
            ("absolute_expires_at", "absolute"),
        ):
            value = session.get(key)
            if not isinstance(value, str):
                return None
            try:
                expires = _dt.datetime.fromisoformat(value)
            except ValueError:
                return None
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=_dt.UTC)
            if now >= expires:
                self.sessions.revoke(token)
                return None
        if not session["active"] or session.get("revoked_at") is not None:
            self.sessions.revoke(token)
            return None
        self.sessions.touch(token, idle_minutes=self.idle_minutes)
        return session

    def require_csrf(self, token: str, csrf_value: str) -> bool:
        session = self.sessions.get(token)
        if session is None:
            return False
        return csrf_value_matches(csrf_value, str(session["csrf_token_hash"]))

    def revoke_user_sessions(self, user_id: int) -> int:
        return self.sessions.revoke_all_for_user(user_id)

    def rotate_session(self, token: str) -> str | None:
        """Session fixation protection on privilege change (not used for login)."""
        session = self.sessions.get(token)
        if session is None:
            return None
        self.sessions.revoke(token)
        new_token = generate_opaque_token()
        csrf = generate_opaque_token()
        self.sessions.create(
            user_id=int(session["user_id"]),
            token=new_token,
            csrf_value=csrf,
            ip_hash=session.get("ip_hash"),
            user_agent=str(session.get("user_agent") or ""),
            absolute_hours=self.absolute_hours,
            idle_minutes=self.idle_minutes,
        )
        return new_token

    @staticmethod
    def public_user(user: dict[str, Any]) -> dict[str, Any]:
        return {
            "user_id": user["user_id"],
            "username": user["username"],
            "role": user["role"],
            "active": bool(user["active"]),
            "created_at": user["created_at"],
            "last_login_at": user["last_login_at"],
        }
