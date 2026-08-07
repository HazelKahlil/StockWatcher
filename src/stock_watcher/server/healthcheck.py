"""Container healthchecks.

Usage:
    python -m stock_watcher.server.healthcheck web
    python -m stock_watcher.server.healthcheck worker
"""
from __future__ import annotations

import json
import sys
from datetime import datetime

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
    store = SQLiteStore(settings.db_path)
    if not store.path.is_file() or not _schema_ok(store):
        print("web not ready: database/schema invalid", file=sys.stderr)
        return 1
    print("web ready")
    return 0


def check_worker(settings: ServerSettings) -> int:
    store = SQLiteStore(settings.db_path)
    if not _schema_ok(store):
        print("worker not ready: database/schema invalid", file=sys.stderr)
        return 1
    with store.connect() as connection:
        row = connection.execute(
            "SELECT holder_id, heartbeat_at, expires_at FROM service_leases "
            "WHERE lease_name = 'stockwatcher-worker'"
        ).fetchone()
    if row is None:
        print("worker not ready: no lease", file=sys.stderr)
        return 1
    holder_id, heartbeat_at, expires_at = row
    now = datetime.now()
    try:
        heartbeat = datetime.fromisoformat(heartbeat_at)
        expires = datetime.fromisoformat(expires_at)
    except ValueError:
        print("worker not ready: invalid lease timestamps", file=sys.stderr)
        return 1
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=now.tzinfo)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=now.tzinfo)
    if now > expires:
        print("worker not ready: lease expired", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "ready",
                "holder_id": holder_id,
                "heartbeat_age_seconds": max(0.0, (now - heartbeat).total_seconds()),
            }
        )
    )
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
