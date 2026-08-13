"""Server configuration from the environment.

Secrets are never configured here: the master key lives in a Docker secret
file and tokens are entered by the Owner in the HTTPS admin page.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from ipaddress import ip_network
from pathlib import Path
from urllib.parse import urlparse


def _csv_env(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in os.environ.get(name, default).split(",")
        if value.strip()
    )


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class Argon2Config:
    time_cost: int = 3
    memory_cost_kib: int = 65536
    parallelism: int = 2


@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    login_account_max: int = 5
    login_ip_max: int = 20
    login_global_max: int = 60
    login_window_seconds: float = 300.0
    command_max: int = 20
    command_window_seconds: float = 60.0
    websocket_connect_max: int = 10
    websocket_connect_window_seconds: float = 60.0
    max_keys: int = 4096


@dataclass(frozen=True, slots=True)
class ServerSettings:
    environment: str = field(
        default_factory=lambda: os.environ.get("STOCKWATCHER_ENV", "production")
    )
    db_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("STOCKWATCHER_DB_PATH", "state/db/stockwatcher.db")
        )
    )
    report_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("STOCKWATCHER_REPORT_DIR", "state/reports")
        )
    )
    backup_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("STOCKWATCHER_BACKUP_DIR", "/backups")
        )
    )
    master_key_file: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["STOCKWATCHER_MASTER_KEY_FILE"])
            if os.environ.get("STOCKWATCHER_MASTER_KEY_FILE")
            else None
        )
    )
    business_timezone: str = os.environ.get(
        "STOCKWATCHER_BUSINESS_TIMEZONE", "Asia/Shanghai"
    )
    public_origin: str = field(
        default_factory=lambda: os.environ.get(
            "STOCKWATCHER_PUBLIC_ORIGIN", "http://127.0.0.1:8000"
        )
    )
    trusted_proxy_cidrs: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("STOCKWATCHER_TRUSTED_PROXY_CIDRS")
    )
    trusted_hosts: tuple[str, ...] = field(
        default_factory=lambda: _csv_env(
            "STOCKWATCHER_TRUSTED_HOSTS", "localhost,127.0.0.1,web,testserver"
        )
    )
    request_body_limit_bytes: int = int(
        os.environ.get("STOCKWATCHER_REQUEST_BODY_LIMIT_BYTES", str(1024 * 1024))
    )
    login_hash_concurrency: int = int(
        os.environ.get("STOCKWATCHER_LOGIN_HASH_CONCURRENCY", "2")
    )
    session_touch_interval_seconds: float = float(
        os.environ.get("STOCKWATCHER_SESSION_TOUCH_INTERVAL_SECONDS", "300")
    )
    websocket_auth_check_seconds: float = float(
        os.environ.get("STOCKWATCHER_WS_AUTH_CHECK_SECONDS", "20")
    )
    websocket_max_per_user: int = int(
        os.environ.get("STOCKWATCHER_WS_MAX_PER_USER", "5")
    )
    websocket_max_per_ip: int = int(
        os.environ.get("STOCKWATCHER_WS_MAX_PER_IP", "10")
    )
    websocket_max_global: int = int(
        os.environ.get("STOCKWATCHER_WS_MAX_GLOBAL", "100")
    )
    retention_enabled: bool = field(
        default_factory=lambda: _bool_env("STOCKWATCHER_RETENTION_ENABLED")
    )
    session_retention_days: int = int(
        os.environ.get("STOCKWATCHER_SESSION_RETENTION_DAYS", "30")
    )
    command_retention_days: int = int(
        os.environ.get("STOCKWATCHER_COMMAND_RETENTION_DAYS", "90")
    )
    audit_retention_days: int = int(
        os.environ.get("STOCKWATCHER_AUDIT_RETENTION_DAYS", "180")
    )
    security_audit_retention_days: int = int(
        os.environ.get("STOCKWATCHER_SECURITY_AUDIT_RETENTION_DAYS", "365")
    )
    session_absolute_hours: float = float(
        os.environ.get("STOCKWATCHER_SESSION_ABSOLUTE_HOURS", "12")
    )
    session_idle_minutes: float = float(
        os.environ.get("STOCKWATCHER_SESSION_IDLE_MINUTES", "120")
    )
    log_level: str = os.environ.get("STOCKWATCHER_LOG_LEVEL", "INFO")
    secret_key_version: int = int(
        os.environ.get("STOCKWATCHER_SECRET_KEY_VERSION", "1")
    )
    source_commit: str = os.environ.get(
        "STOCKWATCHER_SOURCE_COMMIT", "unknown"
    )
    build_version: str = os.environ.get(
        "STOCKWATCHER_BUILD_VERSION", "web-internal-test-v1"
    )
    # These are liveness controls for the unique Worker, not provider
    # credentials or business-rule parameters.  A scan may be slow, but it
    # must never make a healthy lease look like a healthy Worker forever.
    worker_loop_stale_seconds: float = float(
        os.environ.get("STOCKWATCHER_WORKER_LOOP_STALE_SECONDS", "30")
    )
    worker_scan_timeout_seconds: float = float(
        os.environ.get("STOCKWATCHER_WORKER_SCAN_TIMEOUT_SECONDS", "300")
    )
    worker_watchdog_grace_seconds: float = float(
        os.environ.get("STOCKWATCHER_WORKER_WATCHDOG_GRACE_SECONDS", "8")
    )
    argon2: Argon2Config = Argon2Config()
    rate_limits: RateLimitConfig = RateLimitConfig()

    @classmethod
    def from_env(cls) -> ServerSettings:
        return cls()

    def require_master_key(self) -> Path:
        if self.master_key_file is None:
            raise RuntimeError(
                "STOCKWATCHER_MASTER_KEY_FILE must point to the Docker secret file"
            )
        return self.master_key_file

    def validate_for_web(self) -> None:
        """Fail closed on production-facing Web security configuration."""
        if self.environment not in {"production", "development", "test"}:
            raise RuntimeError("STOCKWATCHER_ENV must be production, development, or test")
        origin = urlparse(self.public_origin)
        if origin.scheme not in {"http", "https"} or not origin.hostname:
            raise RuntimeError("STOCKWATCHER_PUBLIC_ORIGIN must be an absolute HTTP(S) origin")
        try:
            origin.port
        except ValueError as error:
            raise RuntimeError("STOCKWATCHER_PUBLIC_ORIGIN has an invalid port") from error
        if origin.username or origin.password or origin.query or origin.fragment:
            raise RuntimeError("STOCKWATCHER_PUBLIC_ORIGIN must not contain credentials or query")
        if origin.path not in {"", "/"}:
            raise RuntimeError("STOCKWATCHER_PUBLIC_ORIGIN must not contain a path")
        if self.environment == "production" and origin.scheme != "https":
            raise RuntimeError("production Web requires an HTTPS public origin")
        if self.request_body_limit_bytes < 1024 or self.request_body_limit_bytes > 16 * 1024 * 1024:
            raise RuntimeError("request body limit must be between 1 KiB and 16 MiB")
        if self.login_hash_concurrency < 1 or self.login_hash_concurrency > 16:
            raise RuntimeError("login hash concurrency must be between 1 and 16")
        for cidr in self.trusted_proxy_cidrs:
            network = ip_network(cidr, strict=False)
            if self.environment == "production" and network.prefixlen == 0:
                raise RuntimeError("production must not trust every address as a proxy")
        if self.environment == "production" and "*" in self.trusted_hosts:
            raise RuntimeError("production must not allow every Host header")
        if not 0 < self.websocket_auth_check_seconds <= 60:
            raise RuntimeError("WebSocket auth checks must run every 60 seconds or less")
        if not 0 <= self.session_touch_interval_seconds <= 3600:
            raise RuntimeError("session touch interval must be between 0 and 3600 seconds")
        for value in (
            self.websocket_max_per_user,
            self.websocket_max_per_ip,
            self.websocket_max_global,
        ):
            if value < 1:
                raise RuntimeError("WebSocket connection limits must be positive")
        if self.websocket_max_per_user > self.websocket_max_global:
            raise RuntimeError("per-user WebSocket limit cannot exceed global limit")
        if self.websocket_max_per_ip > self.websocket_max_global:
            raise RuntimeError("per-IP WebSocket limit cannot exceed global limit")
        if any(
            value < 1
            for value in (
                self.session_retention_days,
                self.command_retention_days,
                self.audit_retention_days,
                self.security_audit_retention_days,
            )
        ):
            raise RuntimeError("retention periods must be positive")

    def allowed_hosts(self) -> tuple[str, ...]:
        public_host = urlparse(self.public_origin).hostname
        values = (*self.trusted_hosts, *(value for value in (public_host,) if value))
        return tuple(dict.fromkeys(value.casefold() for value in values))
