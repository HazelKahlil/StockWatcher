"""Container healthchecks.

Usage:
    python -m stock_watcher.server.healthcheck web
    python -m stock_watcher.server.healthcheck worker
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any

from stock_watcher.storage import SQLiteStore

from .config import ServerSettings


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
    with store.connect() as connection:
        lease = connection.execute(
            "SELECT heartbeat_at, expires_at FROM service_leases "
            "WHERE lease_name = 'stockwatcher-worker'"
        ).fetchone()
        session = connection.execute(
            "SELECT session_id, started_at, last_heartbeat_at FROM runtime_sessions "
            "WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    if lease is None:
        return False, {"reason": "no lease", "worker_lease_held": False}
    try:
        heartbeat = datetime.fromisoformat(str(lease[0]))
        expires = datetime.fromisoformat(str(lease[1]))
    except ValueError:
        return False, {"reason": "invalid lease timestamps", "worker_lease_held": False}
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=now.tzinfo)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=now.tzinfo)
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
    try:
        runtime_heartbeat = datetime.fromisoformat(str(runtime_heartbeat_at))
        session_started = datetime.fromisoformat(str(started_at))
    except ValueError:
        status["reason"] = "invalid runtime timestamps"
        return False, status
    if runtime_heartbeat.tzinfo is None:
        runtime_heartbeat = runtime_heartbeat.replace(tzinfo=now.tzinfo)
    if session_started.tzinfo is None:
        session_started = session_started.replace(tzinfo=now.tzinfo)
    runtime_age = max(0.0, (now - runtime_heartbeat).total_seconds())
    status["runtime_heartbeat_age_seconds"] = runtime_age
    if runtime_age > max(15.0, settings.worker_loop_stale_seconds):
        status["reason"] = "runtime heartbeat stale"
        return False, status

    with store.connect() as connection:
        loop_row = connection.execute(
            "SELECT occurred_at FROM runtime_events "
            "WHERE session_id = ? AND event_type = 'worker.loop' "
            "ORDER BY event_id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
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

    loop_timestamp = str(loop_row[0]) if loop_row is not None else str(session_started)
    try:
        loop_at = datetime.fromisoformat(loop_timestamp)
    except ValueError:
        status["reason"] = "invalid Worker loop timestamp"
        return False, status
    if loop_at.tzinfo is None:
        loop_at = loop_at.replace(tzinfo=now.tzinfo)
    loop_age = max(0.0, (now - loop_at).total_seconds())
    status["worker_loop_age_seconds"] = loop_age
    if loop_age > settings.worker_loop_stale_seconds:
        status["reason"] = "Worker main loop stale"
        return False, status

    active_scan_age: float | None = None
    if (
        started_row is not None
        and (finished_row is None or int(started_row[0]) > int(finished_row[0]))
    ):
        try:
            scan_started = datetime.fromisoformat(str(started_row[1]))
        except ValueError:
            status["reason"] = "invalid Worker scan timestamp"
            return False, status
        if scan_started.tzinfo is None:
            scan_started = scan_started.replace(tzinfo=now.tzinfo)
        active_scan_age = max(0.0, (now - scan_started).total_seconds())
    if attempt_row is not None:
        try:
            attempt_started = datetime.fromisoformat(str(attempt_row[0]))
        except ValueError:
            status["reason"] = "invalid scan attempt timestamp"
            return False, status
        if attempt_started.tzinfo is None:
            attempt_started = attempt_started.replace(tzinfo=now.tzinfo)
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
