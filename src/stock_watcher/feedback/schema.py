"""Explicit, additive feedback extension for StockWatcher core schema v10.

No request handler executes DDL. The main schema version is deliberately not
changed: old v10 Web/Worker builds ignore these independent tables. A release
owner must approve and run the migration on an offline copy before production.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

EXTENSION_VERSION = 1
CORE_VERSION = 10
TABLE_COLUMNS = {
    "web_candidate_approval_schema": ("singleton", "version", "installed_at"),
    "web_candidate_approvals": (
        "user_id", "trade_date", "code", "selected", "version",
        "first_selected_at", "updated_at", "last_event_id",
    ),
    "web_candidate_approval_events": (
        "event_id", "user_id", "trade_date", "code", "snapshot_id", "action",
        "recorded_at", "version", "request_id", "context_json",
    ),
    "web_candidate_approval_requests": (
        "user_id", "request_id", "request_hash", "trade_date", "code",
        "created_at", "receipt_json",
    ),
}
DDL = (
    """CREATE TABLE web_candidate_approval_schema (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        version INTEGER NOT NULL CHECK(version = 1),
        installed_at TEXT NOT NULL
    )""",
    """CREATE TABLE web_candidate_approvals (
        user_id INTEGER NOT NULL,
        trade_date TEXT NOT NULL,
        code TEXT NOT NULL,
        selected INTEGER NOT NULL CHECK(selected IN (0, 1)),
        version INTEGER NOT NULL CHECK(version >= 1),
        first_selected_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_event_id INTEGER NOT NULL,
        PRIMARY KEY(user_id, trade_date, code)
    )""",
    """CREATE TABLE web_candidate_approval_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        trade_date TEXT NOT NULL,
        code TEXT NOT NULL,
        snapshot_id INTEGER NOT NULL,
        action TEXT NOT NULL CHECK(action IN ('approve', 'revoke')),
        recorded_at TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version >= 1),
        request_id TEXT NOT NULL,
        context_json TEXT NOT NULL,
        UNIQUE(user_id, request_id),
        UNIQUE(user_id, trade_date, code, version)
    )""",
    """CREATE TABLE web_candidate_approval_requests (
        user_id INTEGER NOT NULL,
        request_id TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        trade_date TEXT NOT NULL,
        code TEXT NOT NULL,
        created_at TEXT NOT NULL,
        receipt_json TEXT NOT NULL,
        PRIMARY KEY(user_id, request_id)
    )""",
    "CREATE INDEX idx_approval_events_user ON web_candidate_approval_events(user_id, event_id)",
    "CREATE INDEX idx_approval_events_date ON web_candidate_approval_events(trade_date, event_id)",
    "CREATE INDEX idx_approval_requests_rate "
    "ON web_candidate_approval_requests(user_id, created_at)",
)


class SchemaUnavailable(RuntimeError):
    """The extension is absent, partial, unsupported or structurally different."""


@contextmanager
def database(path: Path, *, write: bool = False) -> Iterator[sqlite3.Connection]:
    """Open an existing file only; never create a business database by accident."""
    mode = "rw" if write else "ro"
    uri = Path(path).resolve().as_uri() + f"?mode={mode}"
    with closing(sqlite3.connect(uri, uri=True, timeout=1.0)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=1000")
        connection.execute("PRAGMA foreign_keys=ON")
        if write:
            connection.execute("PRAGMA synchronous=FULL")
        else:
            connection.execute("PRAGMA query_only=ON")
        yield connection


def check_core(connection: sqlite3.Connection) -> None:
    try:
        row = connection.execute(
            "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row is None or int(row[0]) != CORE_VERSION:
            raise SchemaUnavailable("feedback kit requires reviewed core schema v10")
        required = {
            "web_users": {"user_id", "active", "role"},
            "web_public_state": {"state_key", "snapshot_id"},
            "candidate_snapshots": {
                "id", "source_ts", "generated_at", "health", "provider_version",
                "config_version", "app_version", "overall_weak",
            },
            "candidate_items": {
                "snapshot_id", "code", "name", "rank", "price", "change_pct",
                "level", "sector_name", "fund_label", "payload_json",
            },
        }
        for table, names in required.items():
            columns = {str(r[1]) for r in connection.execute(f"PRAGMA table_info({table})")}
            if not names <= columns:
                raise SchemaUnavailable(f"incompatible core table: {table}")
    except sqlite3.DatabaseError as error:
        raise SchemaUnavailable("core schema is unavailable") from error


def check_extension(connection: sqlite3.Connection) -> None:
    try:
        for table, expected in TABLE_COLUMNS.items():
            columns = tuple(str(r[1]) for r in connection.execute(f"PRAGMA table_info({table})"))
            if columns != expected:
                raise SchemaUnavailable("approval extension requires explicit migration/review")
        row = connection.execute(
            "SELECT version FROM web_candidate_approval_schema WHERE singleton = 1"
        ).fetchone()
        if row is None or int(row[0]) != EXTENSION_VERSION:
            raise SchemaUnavailable("unsupported approval extension version")
        for table, columns in (
            ("web_candidate_approvals", ("user_id", "trade_date", "code")),
            ("web_candidate_approval_requests", ("user_id", "request_id")),
        ):
            unique_columns = []
            for index in connection.execute(f"PRAGMA index_list({table})"):
                if index[2]:
                    # Index names here come exclusively from sqlite_master.
                    name = str(index[1]).replace('"', '""')
                    unique_columns.append(tuple(
                        str(r[2]) for r in connection.execute(f'PRAGMA index_info("{name}")')
                    ))
            if columns not in unique_columns:
                raise SchemaUnavailable("approval uniqueness constraint missing")
    except sqlite3.DatabaseError as error:
        raise SchemaUnavailable("approval extension is unavailable") from error


def install_on_connection(connection: sqlite3.Connection) -> bool:
    """Called only by explicit migration tooling; uses an atomic DDL transaction."""
    check_core(connection)
    names = {
        str(r[0]) for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    present = names & set(TABLE_COLUMNS)
    if present:
        check_extension(connection)  # Do not 'repair' an unknown partial schema.
        return False
    if connection.in_transaction:
        raise RuntimeError("migration requires its own transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in DDL:
            connection.execute(statement)  # executescript would implicitly commit.
        connection.execute(
            "INSERT INTO web_candidate_approval_schema VALUES (1, ?, ?)",
            (EXTENSION_VERSION, datetime.now(UTC).isoformat()),
        )
        check_extension(connection)
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("migration integrity verification failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("foreign key verification failed")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return True


def migrate_with_backup(path: Path, backup_dir: Path) -> dict[str, object]:
    """Only an explicitly approved, offline DB may be passed by the CLI."""
    path = path.resolve(strict=True)
    with database(path) as source:
        check_core(source)
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("refusing to migrate a damaged database")
        # Idempotent re-run does not create another backup or rewrite metadata.
        existing = source.execute(
            "SELECT 1 FROM sqlite_master WHERE name='web_candidate_approval_schema'"
        ).fetchone()
        if existing:
            check_extension(source)
            return {"changed": False, "extension_version": EXTENSION_VERSION}
        backup_dir = backup_dir.resolve()
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        backup = backup_dir / f"before-approvals-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
        # Exclusive creation prevents overwriting another backup, even on collision.
        with backup.open("xb"):
            pass
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
            target.execute("PRAGMA synchronous=FULL")
            if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RuntimeError("backup integrity verification failed")
        with backup.open("r+b" if os.name == "nt" else "rb") as handle:
            os.fsync(handle.fileno())
        digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    with database(path, write=True) as connection:
        changed = install_on_connection(connection)
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("post-migration integrity verification failed; retain backup")
    return {
        "changed": changed, "extension_version": EXTENSION_VERSION,
        "core_schema_version_unchanged": CORE_VERSION,
        "backup": str(backup), "backup_sha256": digest,
    }
