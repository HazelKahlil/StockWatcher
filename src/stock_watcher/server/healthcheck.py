"""Container healthchecks.

Usage:
    python -m stock_watcher.server.healthcheck web
    python -m stock_watcher.server.healthcheck worker
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from stock_watcher.storage import SQLiteStore

from .config import ServerSettings

READINESS_BUSY_TIMEOUT_MS = 5000

_readiness_stage: ContextVar[str] = ContextVar(
    "readiness_stage",
    default="build_response",
)


def readiness_stage() -> str:
    """Return the current readiness probe stage for structured failure logs."""
    return _readiness_stage.get()


def set_readiness_stage(stage: str) -> None:
    _readiness_stage.set(stage)


@contextmanager
def health_connection(store: SQLiteStore) -> Iterator[sqlite3.Connection]:
    """Open a health-check cursor and close ephemeral read-only connections.

    Write stores keep a per-thread connection; closing those would poison the
    Worker. Read-only stores create a new connection on every ``connect()`` and
    must be closed so HTTP readiness cannot leak descriptors. ``busy_timeout``
    matches the write-store default so a brief WAL writer does not become an
    immediate ``OperationalError`` on the public probe.
    """
    connection = store.connect()
    try:
        connection.execute(f"PRAGMA busy_timeout={READINESS_BUSY_TIMEOUT_MS}")
        yield connection
    finally:
        if store.read_only:
            connection.close()


def _schema_ok(store: SQLiteStore) -> bool:
    with store.connect() as connection:
        row = connection.execute(
            "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
    return (
        row is not None
        and int(row[0]) == store.CURRENT_SCHEMA_VERSION
        and integrity == ("ok",)
    )


def check_web(settings: ServerSettings) -> int:
    store = SQLiteStore(settings.db_path, read_only=True)
    if not store.path.is_file() or not _schema_ok(store):
        print("web not ready: database/schema invalid", file=sys.stderr)
        return 1
    print("web ready")
    return 0


def _aware_datetime(value: object, now: datetime) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    return parsed


def worker_readiness(
    store: SQLiteStore,
    settings: ServerSettings,
) -> tuple[bool, dict[str, Any]]:
    """Check lease plus evidence that the Worker main loop is progressing.

    A lease heartbeat is intentionally maintained by a separate thread.  It
    proves fencing is alive, but it does not prove that the scan scheduler can
    claim commands or finish a provider operation.  Runtime session and
    ``worker.*`` events provide that second liveness boundary without adding a
    schema migration to the already deployed database.
    """
    now = datetime.now().astimezone()
    set_readiness_stage("read_worker_lease")
    with health_connection(store) as connection:
        lease = connection.execute(
            "SELECT heartbeat_at, expires_at FROM service_leases "
            "WHERE lease_name = 'stockwatcher-worker'"
        ).fetchone()
        set_readiness_stage("read_runtime_session")
        session = connection.execute(
            "SELECT session_id, started_at, last_heartbeat_at FROM runtime_sessions "
            "WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    if lease is None:
        return False, {"reason": "no lease", "worker_lease_held": False}
    heartbeat = _aware_datetime(lease[0], now)
    expires = _aware_datetime(lease[1], now)
    if heartbeat is None or expires is None:
        return False, {"reason": "invalid lease timestamps", "worker_lease_held": False}
    heartbeat_age = max(0.0, (now - heartbeat).total_seconds())
    status: dict[str, Any] = {
        "worker_lease_held": expires > now,
        "heartbeat_age_seconds": heartbeat_age,
    }
    if expires <= now:
        status["reason"] = "lease expired"
        return False, status
    if session is None:
        status["reason"] = "no active runtime session"
        return False, status

    session_id, started_at, runtime_heartbeat_at = session
    runtime_heartbeat = _aware_datetime(runtime_heartbeat_at, now)
    session_started = _aware_datetime(started_at, now)
    if runtime_heartbeat is None or session_started is None:
        status["reason"] = "invalid runtime timestamps"
        return False, status
    runtime_age = max(0.0, (now - runtime_heartbeat).total_seconds())
    status["runtime_heartbeat_age_seconds"] = runtime_age
    if runtime_age > max(15.0, settings.worker_loop_stale_seconds):
        status["reason"] = "runtime heartbeat stale"
        return False, status

    set_readiness_stage("read_worker_loop")
    with health_connection(store) as connection:
        loop_row = connection.execute(
            "SELECT occurred_at FROM runtime_events "
            "WHERE session_id = ? AND event_type = 'worker.loop' "
            "ORDER BY event_id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        set_readiness_stage("read_active_scan")
        started_row = connection.execute(
            "SELECT event_id, occurred_at FROM runtime_events "
            "WHERE session_id = ? AND event_type = 'worker.scan_started' "
            "ORDER BY event_id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        finished_row = connection.execute(
            "SELECT event_id, occurred_at FROM runtime_events "
            "WHERE session_id = ? AND event_type = 'worker.scan_finished' "
            "ORDER BY event_id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        attempt_row = connection.execute(
            "SELECT started_at FROM scan_attempts "
            "WHERE session_id = ? AND completed_at IS NULL "
            "ORDER BY started_at LIMIT 1",
            (session_id,),
        ).fetchone()

    set_readiness_stage("build_response")
    loop_timestamp = str(loop_row[0]) if loop_row is not None else str(session_started)
    loop_at = _aware_datetime(loop_timestamp, now)
    if loop_at is None:
        status["reason"] = "invalid Worker loop timestamp"
        return False, status
    loop_age = max(0.0, (now - loop_at).total_seconds())
    status["worker_loop_age_seconds"] = loop_age
    if loop_age > settings.worker_loop_stale_seconds:
        status["reason"] = "Worker main loop stale"
        return False, status

    active_scan_age: float | None = None
    if started_row is not None:
        try:
            started_id = int(started_row[0])
            finished_id = int(finished_row[0]) if finished_row is not None else None
        except (TypeError, ValueError):
            status["reason"] = "invalid Worker scan timestamp"
            return False, status
        if finished_id is None or started_id > finished_id:
            scan_started = _aware_datetime(started_row[1], now)
            if scan_started is None:
                status["reason"] = "invalid Worker scan timestamp"
                return False, status
            active_scan_age = max(0.0, (now - scan_started).total_seconds())
    if attempt_row is not None:
        attempt_started = _aware_datetime(attempt_row[0], now)
        if attempt_started is None:
            status["reason"] = "invalid scan attempt timestamp"
            return False, status
        attempt_age = max(0.0, (now - attempt_started).total_seconds())
        active_scan_age = max(active_scan_age or 0.0, attempt_age)
    status["active_scan_age_seconds"] = active_scan_age
    if active_scan_age is not None and active_scan_age > settings.worker_scan_timeout_seconds:
        status["reason"] = "Worker scan stalled"
        return False, status
    return True, status


def check_worker(settings: ServerSettings) -> int:
    store = SQLiteStore(settings.db_path, read_only=True)
    if not store.path.is_file() or not _schema_ok(store):
        print("worker not ready: database/schema invalid", file=sys.stderr)
        return 1
    ready, status = worker_readiness(store, settings)
    if not ready:
        print(f"worker not ready: {status.get('reason', 'unknown')}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "ready", **status}))
    return 0


def main() -> int:
    args = sys.argv[1:]
    if len(args) != 1 or args[0] not in {"web", "worker"}:
        print("usage: healthcheck web|worker", file=sys.stderr)
        return 2
    settings = ServerSettings.from_env()
    if args[0] == "web":
        return check_web(settings)
    return check_worker(settings)


if __name__ == "__main__":
    raise SystemExit(main())
