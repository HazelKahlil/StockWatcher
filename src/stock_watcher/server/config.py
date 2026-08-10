"""Server configuration from the environment.

Secrets are never configured here: the master key lives in a Docker secret
file and tokens are entered by the Owner in the HTTPS admin page.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Argon2Config:
    time_cost: int = 3
    memory_cost_kib: int = 65536
    parallelism: int = 2


@dataclass(frozen=True, slots=True)
class RateLimitConfig:
    login_max: int = 5
    login_window_seconds: float = 300.0
    command_max: int = 20
    command_window_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class ServerSettings:
    environment: str = "production"
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
    public_origin: str = os.environ.get("STOCKWATCHER_PUBLIC_ORIGIN", "http://127.0.0.1:8000")
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
