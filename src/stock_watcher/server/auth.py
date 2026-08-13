"""Authentication: Argon2id passwords, opaque sessions, CSRF, RBAC, rate limits.

Default deny: every API except login/health requires a live session; every
mutation (POST/PATCH/PUT/DELETE) requires a matching ``X-CSRF-Token``.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
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
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 401,
        code: str = "authentication_failed",
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    minimum_length: int = 12
    maximum_length: int = 256
    maximum_username_length: int = 64


@dataclass(frozen=True, slots=True)
class SecurityUpdateResult:
    user: dict[str, Any]
    security_changed: bool
    role_changed: bool
    active_changed: bool
    password_changed: bool
    revoked_sessions: int


@dataclass(slots=True)
class RateLimiter:
    """Thread-safe, memory-bounded sliding-window limiter per key."""

    max_attempts: int = 5
    window_seconds: float = 300.0
    max_keys: int = 4096
    _events: dict[str, deque[float]] = field(default_factory=dict)
    _blocked_until: dict[str, float] = field(default_factory=dict)
    _last_seen: dict[str, float] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def _prune(self, now: float) -> None:
        expired: list[str] = []
        for key, events in self._events.items():
            while events and now - events[0] > self.window_seconds:
                events.popleft()
            if not events and now >= self._blocked_until.get(key, 0.0):
                expired.append(key)
        for key in expired:
            self._events.pop(key, None)
            self._blocked_until.pop(key, None)
            self._last_seen.pop(key, None)

    def _state(self, key: str, now: float) -> deque[float]:
        self._prune(now)
        events = self._events.get(key)
        if events is None:
            if len(self._events) >= self.max_keys:
                raise AuthError(
                    "too many attempts; retry later",
                    status_code=429,
                    code="rate_limited",
                    retry_after=max(1, int(self.window_seconds)),
                )
            events = deque()
            self._events[key] = events
        self._last_seen[key] = now
        return events

    def check(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            events = self._state(key, now)
            blocked_until = self._blocked_until.get(key, 0.0)
            if now < blocked_until or len(events) >= self.max_attempts:
                if now >= blocked_until:
                    blocked_until = now + self.window_seconds
                    self._blocked_until[key] = blocked_until
                raise AuthError(
                    "too many attempts; retry later",
                    status_code=429,
                    code="rate_limited",
                    retry_after=max(1, int(blocked_until - now) + 1),
                )

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            events = self._state(key, now)
            events.append(now)
            if len(events) >= self.max_attempts:
                self._blocked_until[key] = now + self.window_seconds

    def consume(self, key: str) -> None:
        """Check and consume one allowed request from the sliding window."""
        with self._lock:
            self.check(key)
            self.record_failure(key)

    def retry_after(self, key: str) -> int:
        with self._lock:
            return max(0, int(self._blocked_until.get(key, 0.0) - time.monotonic()) + 1)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)
            self._blocked_until.pop(key, None)
            self._last_seen.pop(key, None)


def ip_hash(client_ip: str, key: bytes) -> str:
    return hmac.new(
        key,
        b"stockwatcher-audit-ip-v1\0" + client_ip.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:24]


def csrf_value_matches(provided: str, stored_hash: str) -> bool:
    if not provided:
        return False
    return hashlib.sha256(provided.encode("utf-8")).hexdigest() == stored_hash


def csrf_value_for_session(session_token: str) -> str:
    """Stable per-session CSRF value; safe across multiple browser tabs."""
    return hmac.new(
        session_token.encode("utf-8"),
        b"stockwatcher-csrf-v1",
        hashlib.sha256,
    ).hexdigest()


class AuthService:
    """Server-side session and user management (single web process)."""

    def __init__(
        self,
        store: SQLiteStore,
        *,
        password_hasher: PasswordHasher | None = None,
        absolute_hours: float = 12.0,
        idle_minutes: float = 120.0,
        session_touch_interval_seconds: float = 300.0,
        login_account_limiter: RateLimiter | None = None,
        login_ip_limiter: RateLimiter | None = None,
        login_global_limiter: RateLimiter | None = None,
        command_limiter: RateLimiter | None = None,
        websocket_user_limiter: RateLimiter | None = None,
        websocket_ip_limiter: RateLimiter | None = None,
        websocket_global_limiter: RateLimiter | None = None,
        audit_ip_key: bytes | None = None,
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
        self.session_touch_interval_seconds = session_touch_interval_seconds
        self.login_account_limiter = login_account_limiter or RateLimiter(
            max_attempts=5, window_seconds=300
        )
        self.login_ip_limiter = login_ip_limiter or RateLimiter(
            max_attempts=20, window_seconds=300
        )
        self.login_global_limiter = login_global_limiter or RateLimiter(
            max_attempts=60, window_seconds=300, max_keys=1
        )
        # Compatibility alias for existing operational tests and diagnostics.
        self.login_limiter = self.login_account_limiter
        self.command_limiter = command_limiter or RateLimiter(max_attempts=20, window_seconds=60)
        self.websocket_user_limiter = websocket_user_limiter or RateLimiter(
            max_attempts=10, window_seconds=60
        )
        self.websocket_ip_limiter = websocket_ip_limiter or RateLimiter(
            max_attempts=30, window_seconds=60
        )
        self.websocket_global_limiter = websocket_global_limiter or RateLimiter(
            max_attempts=200, window_seconds=60, max_keys=1
        )
        self._websocket_limit_lock = threading.RLock()
        self.audit_ip_key = audit_ip_key or secrets.token_bytes(32)
        self.policy = password_policy
        self._dummy_password_hash = self.hasher.hash(secrets.token_urlsafe(32))

    # -- passwords -------------------------------------------------------

    def hash_password(self, password: str) -> str:
        if len(password) < self.policy.minimum_length:
            raise AuthError(
                f"password must be at least {self.policy.minimum_length} characters",
                status_code=400,
            )
        if len(password) > self.policy.maximum_length:
            raise AuthError(
                f"password must be at most {self.policy.maximum_length} characters",
                status_code=400,
                code="invalid_password",
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
        if len(normalized) > self.policy.maximum_username_length:
            raise AuthError(
                f"username must be at most {self.policy.maximum_username_length} characters",
                status_code=400,
                code="invalid_username",
            )
        password_hash = self.hash_password(password)
        try:
            user = self.users.create(
                username=normalized,
                password_hash=password_hash,
                role=role,
                created_by=created_by,
            )
        except sqlite3.IntegrityError as error:
            if "web_users.username" not in str(error):
                raise
            raise AuthError(
                "username already exists",
                status_code=409,
                code="username_exists",
            ) from error
        self.audit.record(
            actor_user_id=created_by,
            action="user.create",
            object_type="user",
            object_id=str(user["user_id"]),
            outcome="succeeded",
            detail={"username": normalized, "role": role},
        )
        return user

    def update_user_security(
        self,
        *,
        actor_user_id: int | None,
        user_id: int,
        role: str | None = None,
        active: bool | None = None,
        password_hash: str | None = None,
        audit_action: str = "user.update",
        audit_detail: dict[str, Any] | None = None,
    ) -> SecurityUpdateResult | None:
        """Atomically update security attributes, revoke sessions, and audit."""
        with self.store.transaction(immediate=True) as connection:
            current = self.users.get_by_id(user_id, connection=connection)
            if current is None:
                return None
            role_changed = role is not None and role != str(current["role"])
            active_changed = active is not None and bool(active) != bool(current["active"])
            password_changed = password_hash is not None
            security_changed = role_changed or active_changed or password_changed
            updated = self.users.update(
                user_id,
                role=role,
                active=active,
                password_hash=password_hash,
                protect_last_admin=True,
                connection=connection,
            )
            if updated is None:
                return None
            revoked_sessions = (
                self.sessions.revoke_all_for_user(user_id, connection=connection)
                if security_changed
                else 0
            )
            detail = dict(audit_detail or {})
            if role is not None:
                detail["role"] = role
            if active is not None:
                detail["active"] = bool(active)
            if password_changed:
                detail["password_changed"] = True
            detail["revoked_sessions"] = revoked_sessions
            self.audit.record(
                actor_user_id=actor_user_id,
                action=audit_action,
                object_type="user",
                object_id=str(user_id),
                outcome="succeeded",
                detail=detail,
                connection=connection,
            )
            return SecurityUpdateResult(
                user=updated,
                security_changed=security_changed,
                role_changed=role_changed,
                active_changed=active_changed,
                password_changed=password_changed,
                revoked_sessions=revoked_sessions,
            )

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
        if len(normalized) > self.policy.maximum_username_length:
            raise AuthError("用户名或密码错误", status_code=401, code="invalid_credentials")
        if len(password) > self.policy.maximum_length:
            raise AuthError("用户名或密码错误", status_code=401, code="invalid_credentials")
        account_key = f"account:{normalized or 'empty'}"
        ip_key = f"ip:{client_ip or 'unknown'}"
        global_key = "global"
        for limiter, key in (
            (self.login_global_limiter, global_key),
            (self.login_ip_limiter, ip_key),
            (self.login_account_limiter, account_key),
        ):
            limiter.check(key)
        user = self.users.get_by_username(normalized)
        if user is None or not user["active"]:
            self.verify_password(self._dummy_password_hash, password)
            self._record_login_failure(account_key, ip_key, global_key)
            self.audit.record(
                action="auth.login",
                object_type="user",
                object_id=normalized,
                outcome="denied",
                detail={"reason": "invalid_credentials"},
            )
            raise AuthError("用户名或密码错误", status_code=401, code="invalid_credentials")
        if not self.verify_password(str(user["password_hash"]), password):
            self._record_login_failure(account_key, ip_key, global_key)
            self.audit.record(
                actor_user_id=user["user_id"],
                action="auth.login",
                object_type="user",
                object_id=str(user["user_id"]),
                outcome="denied",
                detail={"reason": "invalid_credentials"},
            )
            raise AuthError("用户名或密码错误", status_code=401, code="invalid_credentials")
        self.login_account_limiter.reset(account_key)
        if self.hasher.check_needs_rehash(str(user["password_hash"])):
            replacement = self.hasher.hash(password)
            self.users.rehash_password(int(user["user_id"]), replacement)
        token = generate_opaque_token()
        csrf = csrf_value_for_session(token)
        self.sessions.create(
            user_id=int(user["user_id"]),
            token=token,
            csrf_value=csrf,
            ip_hash=ip_hash(client_ip, self.audit_ip_key) if client_ip else None,
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

    def _record_login_failure(self, account_key: str, ip_key: str, global_key: str) -> None:
        self.login_account_limiter.record_failure(account_key)
        self.login_ip_limiter.record_failure(ip_key)
        self.login_global_limiter.record_failure(global_key)

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

    def authenticate(self, token: str | None, *, touch: bool = True) -> dict[str, Any] | None:
        """Validate a session token; returns the joined session+user row."""
        if not token:
            return None
        session = self.sessions.get(token)
        if session is None:
            return None
        import datetime as _dt

        now = _dt.datetime.now(_dt.UTC)
        for key in ("idle_expires_at", "absolute_expires_at"):
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
        if touch:
            last_seen_value = session.get("last_seen_at")
            if not isinstance(last_seen_value, str):
                return None
            try:
                last_seen = _dt.datetime.fromisoformat(last_seen_value)
            except ValueError:
                return None
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=_dt.UTC)
            elapsed = (now - last_seen.astimezone(_dt.UTC)).total_seconds()
            if elapsed >= self.session_touch_interval_seconds:
                self.sessions.touch_if_due(
                    token,
                    idle_minutes=self.idle_minutes,
                    minimum_interval_seconds=self.session_touch_interval_seconds,
                )
        return session

    def require_csrf(self, token: str, csrf_value: str) -> bool:
        session = self.sessions.get(token)
        if session is None:
            return False
        return csrf_value_matches(csrf_value, str(session["csrf_token_hash"]))

    def revoke_user_sessions(self, user_id: int) -> int:
        return self.sessions.revoke_all_for_user(user_id)

    def consume_websocket_connection(self, *, user_id: int, client_ip: str) -> None:
        """Atomically check and consume isolated WebSocket connection budgets."""
        limits = (
            (self.websocket_user_limiter, f"user:{user_id}"),
            (self.websocket_ip_limiter, f"ip:{client_ip}"),
            (self.websocket_global_limiter, "global"),
        )
        with self._websocket_limit_lock:
            for limiter, key in limits:
                limiter.check(key)
            for limiter, key in limits:
                limiter.consume(key)

    def rotate_session(self, token: str) -> str | None:
        """Session fixation protection on privilege change (not used for login)."""
        session = self.sessions.get(token)
        if session is None:
            return None
        self.sessions.revoke(token)
        new_token = generate_opaque_token()
        csrf = csrf_value_for_session(new_token)
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
