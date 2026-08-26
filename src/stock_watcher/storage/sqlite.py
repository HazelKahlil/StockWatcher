from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

try:
    _fcntl: Any
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows uses one writer lease path.
    _fcntl = None

if TYPE_CHECKING:
    from stock_watcher.engine.candidates import CandidateBatch


@dataclass(slots=True)
class SQLiteStore:
    path: Path
    read_only: bool = False
    recovery_backup_dirs: tuple[Path, ...] = ()

    CURRENT_SCHEMA_VERSION: ClassVar[int] = 10

    _SQLITE_MAGIC = b"SQLite format 3\x00"
    _OUTCOME_COLUMNS: ClassVar[tuple[str, ...]] = (
        "id",
        "entry_snapshot_id",
        "entry_alert_id",
        "entry_trade_date",
        "slot",
        "rank",
        "code",
        "name",
        "entry_price",
        "entry_source_ts",
        "target_trade_date",
        "target_slot",
        "exit_price",
        "exit_source_ts",
        "return_pct",
        "status",
        "outcome",
        "settlement_method",
        "quality",
        "provider_version",
        "config_version",
        "app_version",
        "created_at",
        "updated_at",
        "safe_reason",
        "settlement_attempts",
        "last_attempt_at",
        "next_retry_at",
    )
    last_recovery: dict[str, object] | None = None
    _integrity_verified_version: int | None = None
    _wal_configured: bool = False
    _wal_lock: threading.Lock = field(default_factory=threading.Lock)
    _thread_local: threading.local = field(default_factory=threading.local)
    _write_guard: Callable[[sqlite3.Connection], None] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def connect(self) -> sqlite3.Connection:
        """Return one connection with the shared SQLite runtime settings.

        Read-write stores keep ONE persistent connection per thread. Rapidly
        opening and closing many short connections from two processes makes
        SQLite delete and recreate the shared ``-shm``/``-wal`` files when the
        last connection closes; a stale view then breaks cross-process write
        locking and can mix pages between tables. A live per-thread connection
        keeps the shared files in place. Read-only stores stay ephemeral.
        """
        if self.read_only:
            connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            connection.execute("PRAGMA foreign_keys=ON")
            return connection
        existing = getattr(self._thread_local, "connection", None)
        if existing is not None:
            return existing  # type: ignore[no-any-return]
        connection = sqlite3.connect(self.path)
        if not self.read_only:
            with self._wal_lock:
                if not self._wal_configured:
                    connection.execute("PRAGMA journal_mode=WAL")
                    self._wal_configured = True
        # NORMAL can acknowledge a committed WAL transaction before the WAL
        # pages are durable.  A forced restart/power loss can then leave a
        # valid SQLite header pointing at malformed pages.  This store is a
        # small, audit-oriented database: prefer FULL durability over the
        # marginal throughput gain of NORMAL.
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        self._thread_local.connection = connection
        return connection

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield one connection with an active read-write transaction.

        Web mutations and the Worker's business writes plus their outbox
        events are committed atomically through this entry point.
        """
        if self.read_only:
            raise RuntimeError("cannot open a write transaction on a read-only store")
        self.initialize()
        with self.connect() as connection:
            with connection:
                if immediate or self._write_guard is not None:
                    connection.execute("BEGIN IMMEDIATE")
                if self._write_guard is not None:
                    self._write_guard(connection)
                yield connection

    def bind_write_guard(
        self,
        guard: Callable[[sqlite3.Connection], None] | None,
    ) -> None:
        """Fence subsequent transactions to the current Worker lease.

        The Web process uses an independent ``SQLiteStore`` and never binds a
        guard. The Worker binds its lease assertion only after acquisition, so
        every guarded transaction first obtains SQLite's write lock and then
        verifies holder, fencing token and expiry in that same transaction.
        """
        self._write_guard = guard

    def initialize(self) -> None:
        self.last_recovery = None
        if not self.read_only and self.path.exists() and not self._looks_like_sqlite():
            try:
                # A live connection may still point at the inode that is about
                # to be quarantined.  Close it before replacing the database,
                # otherwise _initialize_database() would keep using that stale
                # connection and the restored file would not become visible to
                # this process.
                self._close_thread_connection()
                self._restore_from_backup()
                self._initialize_database()
                return
            except (sqlite3.DatabaseError, RuntimeError):
                if self.path.exists():
                    self.read_only = True
                raise
        try:
            self._initialize_database()
        except (sqlite3.DatabaseError, RuntimeError) as error:
            self._close_thread_connection()
            if not self.read_only and self._is_recoverable_database_error(error):
                try:
                    self._restore_from_backup()
                    self._initialize_database()
                    return
                except (sqlite3.DatabaseError, RuntimeError):
                    if self.path.exists():
                        self.read_only = True
                    raise
            if self.path.exists():
                self.read_only = True
            raise

    def _initialize_database(self) -> None:
        with self.connect() as connection:
            has_schema_table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'schema_version'"
            ).fetchone() is not None
            if not has_schema_table:
                # CREATE TABLE writes the schema cookie in the database
                # header; with multiple writer processes it must happen
                # exactly once, never on every connection.
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_version "
                    "(version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
                )
            version = self._schema_version(connection)
            if version < self.CURRENT_SCHEMA_VERSION:
                self._backup_before_migration(version)
                self._migrate_to_current(connection, version)
            if self._integrity_verified_version != self.CURRENT_SCHEMA_VERSION:
                # Full-database integrity scans are expensive and lock
                # sensitive on shared filesystems; verify once per process
                # after reaching the current schema instead of on every
                # store call. SQLite still protects every transaction.
                self._assert_integrity(connection)
                self._integrity_verified_version = self.CURRENT_SCHEMA_VERSION
            self._assert_current_schema(connection)

    @staticmethod
    def _is_recoverable_database_error(error: BaseException) -> bool:
        message = str(error).casefold()
        return any(
            marker in message
            for marker in (
                "database disk image is malformed",
                "file is not a database",
                "file is encrypted or is not a database",
                "database schema is corrupt",
                "database integrity check failed",
                "malformed",
            )
        )

    def _looks_like_sqlite(self) -> bool:
        try:
            with self.path.open("rb") as handle:
                return handle.read(16) == self._SQLITE_MAGIC
        except OSError:
            return False

    @staticmethod
    def _database_sidecars(path: Path) -> tuple[Path, Path]:
        return Path(f"{path}-wal"), Path(f"{path}-shm")

    @classmethod
    def _remove_database_sidecars(cls, path: Path) -> None:
        for sidecar in cls._database_sidecars(path):
            sidecar.unlink(missing_ok=True)

    @classmethod
    def _remove_database_family(cls, path: Path) -> None:
        path.unlink(missing_ok=True)
        cls._remove_database_sidecars(path)

    @classmethod
    def _move_database_family(cls, source: Path, destination: Path) -> None:
        if source.exists():
            source.replace(destination)
        for source_sidecar, destination_sidecar in zip(
            cls._database_sidecars(source),
            cls._database_sidecars(destination),
            strict=True,
        ):
            if source_sidecar.exists():
                source_sidecar.replace(destination_sidecar)

    @staticmethod
    def _validate_database_file(path: Path) -> None:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RuntimeError("database snapshot failed integrity check")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise RuntimeError("database snapshot failed foreign key check")

    def _close_thread_connection(self) -> None:
        connection = getattr(self._thread_local, "connection", None)
        if connection is not None:
            connection.close()
            del self._thread_local.connection
        self._wal_configured = False
        self._integrity_verified_version = None

    @contextmanager
    def _recovery_file_lock(self) -> Iterator[None]:
        """Serialize recovery across the Web and Worker processes."""
        lock_path = self.path.with_name(f"{self.path.name}.recovery.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX)
            try:
                yield
            finally:
                if _fcntl is not None:
                    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)

    def _backup_candidates(self) -> tuple[Path, ...]:
        candidates: set[Path] = set(
            self.path.parent.glob(f"{self.path.name}.pre-v*.bak")
        )
        for root in self.recovery_backup_dirs:
            if root.is_file():
                candidates.add(root)
                continue
            if not root.is_dir():
                continue
            # Admin backups intentionally use a portable database filename and
            # may sit below an operator-created grouping directory, for
            # example ``/backups/predeploy-.../stockwatcher-.../``.  Recovery
            # must discover that contract instead of silently falling back to
            # an older migration backup beside the live database.
            for name in {self.path.name, "stockwatcher.sqlite3"}:
                candidates.update(root.rglob(name))
        live_path = self.path.resolve()
        candidates = {
            candidate
            for candidate in candidates
            if candidate.resolve() != live_path
        }
        return tuple(
            sorted(
                candidates,
                key=lambda candidate: candidate.stat().st_mtime
                if candidate.exists()
                else 0.0,
                reverse=True,
            )
        )

    @staticmethod
    def _fsync_file(path: Path) -> None:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _schema_version_from_file(path: Path) -> int | None:
        try:
            with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
                row = connection.execute(
                    "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        return int(row[0]) if row is not None else None

    def _restore_from_backup(self) -> None:
        """Recover a damaged database file from the newest verified backup.

        The damaged database family, including stale WAL/SHM sidecars, is
        preserved for forensics instead of being overwritten.  Recovery is
        serialized across the Web and Worker so a second process never copies
        a second backup over the first process's restored database.
        """
        with self._recovery_file_lock():
            # Another process may have repaired the file while this process
            # waited for the lock.  Re-check before quarantining anything.
            if self.path.exists() and self._looks_like_sqlite():
                try:
                    self._validate_database_file(self.path)
                except (sqlite3.DatabaseError, RuntimeError):
                    pass
                else:
                    return

            staging = self.path.with_name(f"{self.path.name}.recovery-tmp")
            for backup in self._backup_candidates():
                try:
                    with backup.open("rb") as handle:
                        if handle.read(16) != self._SQLITE_MAGIC:
                            continue
                    self._validate_database_file(backup)
                    self._remove_database_family(staging)
                    shutil.copy2(backup, staging)
                    self._validate_database_file(staging)
                    self._fsync_file(staging)
                except (OSError, sqlite3.DatabaseError, RuntimeError):
                    self._remove_database_family(staging)
                    continue

                corrupt = self.path.with_suffix(f"{self.path.suffix}.corrupt")
                if corrupt.exists() or any(
                    sidecar.exists() for sidecar in self._database_sidecars(corrupt)
                ):
                    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
                    corrupt = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
                self._move_database_family(self.path, corrupt)
                self._remove_database_sidecars(self.path)
                staging.replace(self.path)
                self._fsync_directory(self.path.parent)
                match = re.search(r"pre-v(\d+)\.bak$", backup.name)
                source_backup = (
                    backup.name
                    if backup.parent == self.path.parent
                    else str(backup)
                )
                self.last_recovery = {
                    "restored_at": datetime.now().isoformat(),
                    "source_backup": source_backup,
                    "restored_schema_version": (
                        int(match.group(1))
                        if match
                        else self._schema_version_from_file(backup)
                    ),
                    "preserved_corrupt_file": corrupt.name,
                }
                return

            self._remove_database_family(staging)
            self.read_only = True
            self.last_recovery = {
                "restored": False,
                "reason": "database is corrupt and no valid backup exists",
            }
            raise sqlite3.DatabaseError(self.last_recovery["reason"])

    def _schema_version(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        return 0 if row is None else int(row[0])

    def _backup_before_migration(self, version: int) -> None:
        """Keep a durable snapshot before changing an existing database."""
        if not self.path.exists() or version == 0:
            return
        backup = self.path.with_suffix(f"{self.path.suffix}.pre-v{version + 1}.bak")
        staging = backup.with_name(f"{backup.name}.tmp")
        self._remove_database_family(staging)
        with closing(sqlite3.connect(self.path)) as source, closing(
            sqlite3.connect(staging)
        ) as target:
            source.backup(target)
            target.commit()
        self._validate_database_file(staging)
        self._remove_database_sidecars(backup)
        staging.replace(backup)

    def _migrate_to_current(self, connection: sqlite3.Connection, version: int) -> None:
        if version not in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9):
            raise RuntimeError(f"unsupported schema version: {version}")
        try:
            connection.execute("BEGIN IMMEDIATE")
            if version == 0:
                self._apply_v1_schema(connection)
            if version <= 1:
                self._apply_v2_migration(connection)
            if version <= 2:
                self._apply_v3_migration(connection)
            if version <= 3:
                self._apply_v4_migration(connection)
            if version <= 4:
                self._apply_v5_migration(connection)
            if version <= 5:
                self._apply_v6_migration(connection)
            if version <= 6:
                self._apply_v7_migration(connection)
            if version <= 7:
                self._apply_v8_migration(connection)
            if version <= 8:
                self._apply_v9_migration(connection)
            if version <= 9:
                self._apply_v10_migration(connection)
            connection.execute("DELETE FROM schema_version")
            connection.execute(
                "INSERT INTO schema_version VALUES (?, datetime('now'))",
                (self.CURRENT_SCHEMA_VERSION,),
            )
            self._assert_current_schema(connection)
            connection.commit()
        except (sqlite3.DatabaseError, RuntimeError):
            connection.rollback()
            raise

    @staticmethod
    def _apply_v1_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS notes (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )

    @staticmethod
    def _apply_v2_migration(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS config_versions "
            "(version TEXT PRIMARY KEY, source TEXT NOT NULL, settings_json TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS candidate_snapshots "
            "(id INTEGER PRIMARY KEY, source_ts TEXT NOT NULL, generated_at TEXT NOT NULL, "
            "health TEXT NOT NULL, overall_weak INTEGER NOT NULL, "
            "provider_version TEXT NOT NULL, config_version TEXT NOT NULL, "
            "app_version TEXT NOT NULL, payload_json TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS alert_events "
            "(id INTEGER PRIMARY KEY, snapshot_id INTEGER NOT NULL, "
            "displayed_at TEXT NOT NULL, decision TEXT NOT NULL, channel TEXT NOT NULL, "
            "FOREIGN KEY(snapshot_id) REFERENCES candidate_snapshots(id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS health_metrics "
            "(id INTEGER PRIMARY KEY, source_ts TEXT NOT NULL, received_ts TEXT NOT NULL, "
            "state TEXT NOT NULL, provider_version TEXT NOT NULL, "
            "config_version TEXT NOT NULL, detail TEXT NOT NULL)"
        )

    @staticmethod
    def _apply_v3_migration(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS candidate_items "
            "(id INTEGER PRIMARY KEY, snapshot_id INTEGER NOT NULL, rank INTEGER NOT NULL, "
            "code TEXT NOT NULL, name TEXT NOT NULL, level TEXT NOT NULL, "
            "is_formal INTEGER NOT NULL, is_supplement INTEGER NOT NULL, "
            "price REAL NOT NULL, change_pct REAL NOT NULL, sector_code TEXT NOT NULL, "
            "sector_name TEXT NOT NULL, fund_label TEXT NOT NULL, explanation TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, "
            "FOREIGN KEY(snapshot_id) REFERENCES candidate_snapshots(id) ON DELETE CASCADE)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidate_items_snapshot "
            "ON candidate_items(snapshot_id, rank)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS daily_summaries "
            "(trade_date TEXT PRIMARY KEY, generated_at TEXT NOT NULL, "
            "alert_count INTEGER NOT NULL, top_sectors_json TEXT NOT NULL, "
            "repeated_candidates_json TEXT NOT NULL, closing_performance_json TEXT NOT NULL, "
            "fund_summary TEXT NOT NULL, health_summary TEXT NOT NULL, "
            "summary_text TEXT NOT NULL, version TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS app_settings "
            "(key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        alert_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(alert_events)")
        }
        if "trigger_type" not in alert_columns:
            connection.execute(
                "ALTER TABLE alert_events ADD COLUMN trigger_type TEXT NOT NULL "
                "DEFAULT 'intraday'"
            )

    @staticmethod
    def _apply_v4_migration(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS automation_tasks "
            "(task_key TEXT PRIMARY KEY, task_type TEXT NOT NULL, trade_date TEXT NOT NULL, "
            "target_at TEXT NOT NULL, deadline_at TEXT NOT NULL, state TEXT NOT NULL, "
            "attempts INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, "
            "detail TEXT NOT NULL, snapshot_id INTEGER, "
            "FOREIGN KEY(snapshot_id) REFERENCES candidate_snapshots(id))"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_automation_tasks_trade_date "
            "ON automation_tasks(trade_date, task_type)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS scan_runs "
            "(id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT NOT NULL, "
            "trigger_type TEXT NOT NULL, task_key TEXT, health TEXT NOT NULL, source_ts TEXT, "
            "coverage_ratio REAL, elapsed_seconds REAL, source_age_seconds REAL, "
            "detail TEXT NOT NULL, raw_batch_json TEXT, stable_batch_json TEXT, "
            "audit_json TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_scan_runs_completed "
            "ON scan_runs(completed_at, trigger_type)"
        )

    @staticmethod
    def _apply_v5_migration(connection: sqlite3.Connection) -> None:
        """Add durable process and in-flight scan lifecycle evidence."""
        connection.execute(
            "CREATE TABLE IF NOT EXISTS runtime_sessions "
            "(session_id TEXT PRIMARY KEY, pid INTEGER NOT NULL, ppid INTEGER NOT NULL, "
            "app_path TEXT NOT NULL, source_commit TEXT NOT NULL, started_at TEXT NOT NULL, "
            "last_heartbeat_at TEXT NOT NULL, ended_at TEXT, exit_reason TEXT, "
            "graceful_exit INTEGER NOT NULL DEFAULT 0, last_scan_at TEXT, "
            "last_window_activation_at TEXT)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_sessions_started "
            "ON runtime_sessions(started_at)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS scan_attempts "
            "(attempt_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, started_at TEXT NOT NULL, "
            "last_heartbeat_at TEXT NOT NULL, completed_at TEXT, state TEXT NOT NULL, "
            "operation TEXT NOT NULL, thread_name TEXT NOT NULL, timer_active INTEGER NOT NULL, "
            "detail TEXT NOT NULL, recovery_count INTEGER NOT NULL DEFAULT 0, "
            "FOREIGN KEY(session_id) REFERENCES runtime_sessions(session_id))"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_scan_attempts_started "
            "ON scan_attempts(started_at, state)"
        )

    @staticmethod
    def _apply_v6_migration(connection: sqlite3.Connection) -> None:
        """Add structured runtime lifecycle evidence without changing scan semantics."""
        runtime_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(runtime_sessions)")
        }
        for column, definition in (
            ("last_sleep_at", "TEXT"),
            ("last_wake_at", "TEXT"),
            ("previous_session_id", "TEXT"),
            ("previous_unclean_exit", "INTEGER NOT NULL DEFAULT 0"),
            ("watchdog_restart_count", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if column not in runtime_columns:
                connection.execute(
                    f"ALTER TABLE runtime_sessions ADD COLUMN {column} {definition}"
                )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS runtime_events "
            "(event_id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, occurred_at TEXT NOT NULL, "
            "event_type TEXT NOT NULL, detail_json TEXT NOT NULL, "
            "FOREIGN KEY(session_id) REFERENCES runtime_sessions(session_id))"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_runtime_events_session_time "
            "ON runtime_events(session_id, occurred_at)"
        )
        summary_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(daily_summaries)")
        }
        if "catch_up" not in summary_columns:
            connection.execute(
                "ALTER TABLE daily_summaries ADD COLUMN catch_up INTEGER NOT NULL DEFAULT 0"
            )
        alert_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(alert_events)")
        }
        if "detail_json" not in alert_columns:
            connection.execute(
                "ALTER TABLE alert_events ADD COLUMN detail_json TEXT NOT NULL DEFAULT '{}'"
            )

    @staticmethod
    def _apply_v7_migration(connection: sqlite3.Connection) -> None:
        """Add the Web internal-test tables (users, sessions, lease, commands,
        encrypted secrets, outbox events, public state, audit log).

        The schema contract mirrors ``database/007_web_internal_test.sql`` and
        is purely additive; business tables from v1-v6 are never altered. The
        following v8 migration only narrows one outbox dedupe index.
        """
        connection.execute(
            "CREATE TABLE IF NOT EXISTS web_users ("
            "user_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "username TEXT NOT NULL COLLATE NOCASE UNIQUE, "
            "password_hash TEXT NOT NULL, "
            "role TEXT NOT NULL CHECK (role IN ('tester', 'admin')), "
            "active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)), "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, "
            "last_login_at TEXT, "
            "password_changed_at TEXT NOT NULL, "
            "created_by INTEGER, "
            "FOREIGN KEY(created_by) REFERENCES web_users(user_id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS web_sessions ("
            "session_token_hash TEXT PRIMARY KEY CHECK (length(session_token_hash) = 64), "
            "user_id INTEGER NOT NULL, "
            "csrf_token_hash TEXT NOT NULL CHECK (length(csrf_token_hash) = 64), "
            "created_at TEXT NOT NULL, "
            "last_seen_at TEXT NOT NULL, "
            "idle_expires_at TEXT NOT NULL, "
            "absolute_expires_at TEXT NOT NULL, "
            "revoked_at TEXT, "
            "ip_hash TEXT, "
            "user_agent TEXT NOT NULL DEFAULT '', "
            "FOREIGN KEY(user_id) REFERENCES web_users(user_id) ON DELETE CASCADE)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_sessions_user "
            "ON web_sessions(user_id, revoked_at, absolute_expires_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_sessions_expiry "
            "ON web_sessions(absolute_expires_at, idle_expires_at)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS web_user_state ("
            "user_id INTEGER PRIMARY KEY, "
            "last_event_id INTEGER NOT NULL DEFAULT 0, "
            "browser_notifications_enabled INTEGER NOT NULL DEFAULT 0 "
            "CHECK (browser_notifications_enabled IN (0, 1)), "
            "sound_enabled INTEGER NOT NULL DEFAULT 0 CHECK (sound_enabled IN (0, 1)), "
            "updated_at TEXT NOT NULL, "
            "FOREIGN KEY(user_id) REFERENCES web_users(user_id) ON DELETE CASCADE)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS service_leases ("
            "lease_name TEXT PRIMARY KEY, "
            "holder_id TEXT NOT NULL, "
            "source_commit TEXT NOT NULL, "
            "acquired_at TEXT NOT NULL, "
            "heartbeat_at TEXT NOT NULL, "
            "expires_at TEXT NOT NULL, "
            "fencing_token INTEGER NOT NULL DEFAULT 1 CHECK (fencing_token > 0))"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_service_leases_expiry "
            "ON service_leases(expires_at)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS secret_requests ("
            "request_id TEXT PRIMARY KEY, "
            "purpose TEXT NOT NULL CHECK (purpose IN ('token_test', 'token_update')), "
            "ciphertext_b64 TEXT NOT NULL, "
            "nonce_b64 TEXT NOT NULL, "
            "key_version INTEGER NOT NULL CHECK (key_version > 0), "
            "fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 8), "
            "requested_by INTEGER NOT NULL, "
            "created_at TEXT NOT NULL, "
            "expires_at TEXT NOT NULL, "
            "consumed_at TEXT, "
            "status TEXT NOT NULL CHECK (status IN ('pending', 'consumed', 'expired', 'failed')), "
            "FOREIGN KEY(requested_by) REFERENCES web_users(user_id))"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_secret_requests_status_expiry "
            "ON secret_requests(status, expires_at)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS encrypted_secrets ("
            "secret_name TEXT NOT NULL, "
            "slot TEXT NOT NULL CHECK (slot IN ('active', 'previous')), "
            "ciphertext_b64 TEXT NOT NULL, "
            "nonce_b64 TEXT NOT NULL, "
            "key_version INTEGER NOT NULL CHECK (key_version > 0), "
            "fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 8), "
            "status TEXT NOT NULL CHECK (status IN ('active', 'previous', 'revoked')), "
            "updated_by INTEGER, "
            "updated_at TEXT NOT NULL, "
            "last_tested_at TEXT, "
            "capability_json TEXT NOT NULL DEFAULT '{}', "
            "PRIMARY KEY(secret_name, slot), "
            "FOREIGN KEY(updated_by) REFERENCES web_users(user_id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS web_commands ("
            "command_id TEXT PRIMARY KEY, "
            "command_type TEXT NOT NULL CHECK (command_type IN ("
            "'manual_refresh', 'universe_refresh', 'token_test', "
            "'token_update', 'summary_generate')), "
            "status TEXT NOT NULL CHECK (status IN ("
            "'queued', 'running', 'succeeded', 'failed', 'cancelled', 'expired')), "
            "requested_by INTEGER NOT NULL, "
            "requested_at TEXT NOT NULL, "
            "idempotency_key TEXT, "
            "payload_json TEXT NOT NULL DEFAULT '{}', "
            "secret_request_id TEXT, "
            "claimed_by TEXT, "
            "fencing_token INTEGER, "
            "started_at TEXT, "
            "completed_at TEXT, "
            "expires_at TEXT, "
            "attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0), "
            "result_json TEXT, "
            "error_code TEXT, "
            "error_detail TEXT, "
            "FOREIGN KEY(requested_by) REFERENCES web_users(user_id), "
            "FOREIGN KEY(secret_request_id) REFERENCES secret_requests(request_id))"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_web_commands_idempotency "
            "ON web_commands(idempotency_key) WHERE idempotency_key IS NOT NULL"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_web_commands_active_manual_refresh "
            "ON web_commands(command_type) "
            "WHERE command_type = 'manual_refresh' AND status IN ('queued', 'running')"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_commands_claim "
            "ON web_commands(status, requested_at, command_type)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_commands_requester "
            "ON web_commands(requested_by, requested_at)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS web_events ("
            "event_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "event_type TEXT NOT NULL, "
            "occurred_at TEXT NOT NULL, "
            "source_commit TEXT NOT NULL, "
            "correlation_id TEXT, "
            "source_kind TEXT, "
            "source_id TEXT, "
            "visibility TEXT NOT NULL DEFAULT 'all' "
            "CHECK (visibility IN ('all', 'tester', 'admin')), "
            "payload_json TEXT NOT NULL, "
            "expires_at TEXT)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_events_type_time "
            "ON web_events(event_type, occurred_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_events_expiry ON web_events(expires_at)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_web_events_source_dedupe "
            "ON web_events(event_type, source_kind, source_id) "
            "WHERE source_kind IS NOT NULL AND source_id IS NOT NULL"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS web_public_state ("
            "state_key TEXT PRIMARY KEY CHECK (state_key = 'current'), "
            "state_version INTEGER NOT NULL CHECK (state_version >= 0), "
            "snapshot_id INTEGER, "
            "source_ts TEXT, "
            "updated_at TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, "
            "FOREIGN KEY(snapshot_id) REFERENCES candidate_snapshots(id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS web_audit_log ("
            "audit_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "occurred_at TEXT NOT NULL, "
            "actor_user_id INTEGER, "
            "actor_session_hash_prefix TEXT, "
            "action TEXT NOT NULL, "
            "object_type TEXT, "
            "object_id TEXT, "
            "outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed', 'denied')), "
            "request_id TEXT, "
            "detail_json TEXT NOT NULL DEFAULT '{}', "
            "FOREIGN KEY(actor_user_id) REFERENCES web_users(user_id))"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_audit_time ON web_audit_log(occurred_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_audit_actor "
            "ON web_audit_log(actor_user_id, occurred_at)"
        )

    @staticmethod
    def _apply_v8_migration(connection: sqlite3.Connection) -> None:
        """Allow every durable command status transition to reach clients."""
        connection.execute("DROP INDEX IF EXISTS idx_web_events_source_dedupe")
        connection.execute(
            "CREATE UNIQUE INDEX idx_web_events_source_dedupe "
            "ON web_events(event_type, source_kind, source_id) "
            "WHERE source_kind IS NOT NULL AND source_id IS NOT NULL "
            "AND event_type <> 'command.updated'"
        )

    @staticmethod
    def _apply_v9_migration(connection: sqlite3.Connection) -> None:
        """Add independent candidate outcomes without redefining Web schema v8."""
        connection.execute(
            "CREATE TABLE IF NOT EXISTS candidate_outcomes ("
            "id INTEGER PRIMARY KEY, "
            "entry_snapshot_id INTEGER NOT NULL, entry_alert_id INTEGER NOT NULL, "
            "entry_trade_date TEXT NOT NULL, "
            "slot TEXT NOT NULL CHECK(slot IN ('09:45', '14:45')), "
            "rank INTEGER NOT NULL CHECK(rank BETWEEN 1 AND 3), "
            "code TEXT NOT NULL, name TEXT NOT NULL, "
            "entry_price REAL NOT NULL CHECK(entry_price > 0), "
            "entry_source_ts TEXT NOT NULL, target_trade_date TEXT, "
            "target_slot TEXT NOT NULL CHECK(target_slot IN ('09:45', '14:45')), "
            "exit_price REAL, exit_source_ts TEXT, return_pct REAL, "
            "status TEXT NOT NULL CHECK(status IN ('pending', 'settled', 'unavailable')), "
            "outcome TEXT CHECK(outcome IS NULL OR outcome IN ('win', 'loss', 'flat')), "
            "settlement_method TEXT CHECK(settlement_method IS NULL OR settlement_method IN "
            "('realtime_scan', 'realtime_batch', 'historical_minute')), "
            "quality TEXT NOT NULL, provider_version TEXT NOT NULL, "
            "config_version TEXT NOT NULL, app_version TEXT NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, safe_reason TEXT, "
            "settlement_attempts INTEGER NOT NULL DEFAULT 0 CHECK(settlement_attempts >= 0), "
            "last_attempt_at TEXT, next_retry_at TEXT, "
            "UNIQUE(entry_snapshot_id, slot, rank, code), "
            "UNIQUE(entry_alert_id, slot, rank, code))"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidate_outcomes_pending "
            "ON candidate_outcomes(status, target_trade_date, target_slot, next_retry_at, rank)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidate_outcomes_entry_date "
            "ON candidate_outcomes(entry_trade_date DESC, slot, rank)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidate_outcomes_code "
            "ON candidate_outcomes(code, entry_trade_date DESC)"
        )

    @staticmethod
    def _apply_v10_migration(connection: sqlite3.Connection) -> None:
        """Add independent repeat-occurrence tables without touching scoring data."""
        connection.execute(
            "CREATE TABLE IF NOT EXISTS candidate_repeat_days ("
            "id INTEGER PRIMARY KEY, "
            "code TEXT NOT NULL, "
            "name TEXT NOT NULL, "
            "trade_date TEXT NOT NULL, "
            "first_seen_at TEXT NOT NULL, "
            "last_seen_at TEXT NOT NULL, "
            "first_snapshot_id INTEGER, "
            "last_snapshot_id INTEGER, "
            "source_types_json TEXT NOT NULL DEFAULT '[]', "
            "formal_seen INTEGER NOT NULL DEFAULT 0 CHECK (formal_seen IN (0, 1)), "
            "supplement_seen INTEGER NOT NULL DEFAULT 0 CHECK (supplement_seen IN (0, 1)), "
            "count_after INTEGER NOT NULL DEFAULT 0 CHECK (count_after >= 0), "
            "span_days_after INTEGER NOT NULL DEFAULT 0 CHECK (span_days_after >= 0), "
            "active_after INTEGER NOT NULL DEFAULT 0 CHECK (active_after IN (0, 1)), "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, "
            "UNIQUE(code, trade_date))"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidate_repeat_days_date "
            "ON candidate_repeat_days(trade_date, code)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidate_repeat_days_active "
            "ON candidate_repeat_days(active_after, trade_date)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS candidate_repeat_states ("
            "code TEXT PRIMARY KEY, "
            "name TEXT NOT NULL, "
            "active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0, 1)), "
            "window_started_on TEXT, "
            "window_expires_on TEXT, "
            "sequence_started_on TEXT, "
            "occurrence_count INTEGER NOT NULL DEFAULT 0 CHECK (occurrence_count >= 0), "
            "span_days INTEGER NOT NULL DEFAULT 0 CHECK (span_days >= 0), "
            "activated_at TEXT, "
            "activated_trade_date TEXT, "
            "last_seen_on TEXT, "
            "last_seen_at TEXT, "
            "last_snapshot_id INTEGER, "
            "updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_candidate_repeat_states_active "
            "ON candidate_repeat_states(active, last_seen_on)"
        )

    @staticmethod
    def _assert_integrity(connection: sqlite3.Connection) -> None:
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("database integrity check failed")

    def _assert_current_schema(self, connection: sqlite3.Connection) -> None:
        if self._integrity_verified_version == self.CURRENT_SCHEMA_VERSION:
            return
        required_tables = {
            "schema_version",
            "notes",
            "config_versions",
            "candidate_snapshots",
            "candidate_items",
            "alert_events",
            "daily_summaries",
            "app_settings",
            "health_metrics",
            "automation_tasks",
            "scan_runs",
            "runtime_sessions",
            "scan_attempts",
            "runtime_events",
            "web_users",
            "web_sessions",
            "web_user_state",
            "service_leases",
            "web_commands",
            "secret_requests",
            "encrypted_secrets",
            "web_events",
            "web_public_state",
            "web_audit_log",
            "candidate_outcomes",
            "candidate_repeat_days",
            "candidate_repeat_states",
        }
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if (
            self._schema_version(connection) != self.CURRENT_SCHEMA_VERSION
            or not required_tables <= tables
        ):
            raise RuntimeError("schema migration did not reach current version")
        outcome_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(candidate_outcomes)")
        }
        if not set(self._OUTCOME_COLUMNS) <= outcome_columns:
            raise RuntimeError("candidate outcome schema is incomplete")
        self._assert_integrity(connection)

    def start_runtime_session(
        self,
        *,
        session_id: str,
        pid: int,
        ppid: int,
        app_path: str,
        source_commit: str,
        started_at: str,
    ) -> None:
        """Create a process session and close any prior live session as unclean."""
        self.initialize()
        with self.transaction() as connection:
            previous = connection.execute(
                    "SELECT session_id, last_heartbeat_at, last_scan_at "
                    "FROM runtime_sessions WHERE ended_at IS NULL AND session_id <> ?",
                    (session_id,),
                ).fetchall()
            for previous_id, heartbeat, last_scan in previous:
                connection.execute(
                        "UPDATE runtime_sessions SET ended_at = ?, exit_reason = ?, "
                        "graceful_exit = 0, previous_unclean_exit = 1 WHERE session_id = ?",
                        (started_at, "unclean_exit", previous_id),
                    )
                connection.execute(
                        "UPDATE scan_attempts SET completed_at = ?, state = ?, detail = ? "
                        "WHERE session_id = ? AND completed_at IS NULL",
                        (
                            started_at,
                            "aborted",
                            "previous session ended without graceful shutdown",
                            previous_id,
                        ),
                    )
                self._insert_runtime_event(
                        connection,
                        session_id=previous_id,
                        occurred_at=started_at,
                        event_type="unclean_exit",
                        detail={
                            "last_heartbeat_at": heartbeat,
                            "last_scan_at": last_scan,
                        },
                    )
            previous_id = previous[0][0] if previous else None
            connection.execute(
                    "INSERT OR REPLACE INTO runtime_sessions "
                    "(session_id, pid, ppid, app_path, source_commit, started_at, "
                    "last_heartbeat_at, ended_at, exit_reason, graceful_exit, last_scan_at, "
                    "last_window_activation_at, last_sleep_at, last_wake_at, "
                    "previous_session_id, previous_unclean_exit, watchdog_restart_count) VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, NULL, NULL, NULL, NULL, ?, ?, 0)",
                    (
                        session_id,
                        pid,
                        ppid,
                        app_path,
                        source_commit,
                        started_at,
                        started_at,
                        previous_id,
                        int(bool(previous)),
                    ),
                )

    @staticmethod
    def _insert_runtime_event(
        connection: sqlite3.Connection,
        *,
        session_id: str,
        occurred_at: str,
        event_type: str,
        detail: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO runtime_events "
            "(session_id, occurred_at, event_type, detail_json) VALUES (?, ?, ?, ?)",
            (
                session_id,
                occurred_at,
                event_type,
                json.dumps(detail, ensure_ascii=False, sort_keys=True),
            ),
        )

    def record_runtime_event(
        self,
        *,
        session_id: str,
        occurred_at: str,
        event_type: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.initialize()
        with self.transaction() as connection:
            self._insert_runtime_event(
                    connection,
                    session_id=session_id,
                    occurred_at=occurred_at,
                    event_type=event_type,
                    detail=detail or {},
                )
            if event_type == "sleep_detected":
                connection.execute(
                        "UPDATE runtime_sessions SET last_sleep_at = ? WHERE session_id = ?",
                        (occurred_at, session_id),
                    )
            elif event_type == "wake_detected":
                connection.execute(
                        "UPDATE runtime_sessions SET last_wake_at = ? WHERE session_id = ?",
                        (occurred_at, session_id),
                    )

    def list_runtime_events(
        self,
        session_id: str,
    ) -> list[dict[str, Any]]:
        self.initialize()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT event_id, session_id, occurred_at, event_type, detail_json "
                "FROM runtime_events WHERE session_id = ? ORDER BY occurred_at, event_id",
                (session_id,),
            ).fetchall()
        return [
            {
                "event_id": row[0],
                "session_id": row[1],
                "occurred_at": row[2],
                "event_type": row[3],
                "detail": json.loads(row[4]),
            }
            for row in rows
        ]

    def heartbeat_runtime_session(
        self,
        session_id: str,
        heartbeat_at: str,
        *,
        last_scan_at: str | None = None,
        last_window_activation_at: str | None = None,
        last_sleep_at: str | None = None,
        last_wake_at: str | None = None,
    ) -> None:
        self.initialize()
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE runtime_sessions SET last_heartbeat_at = ?, "
                "last_scan_at = COALESCE(?, last_scan_at), "
                "last_window_activation_at = COALESCE(?, last_window_activation_at), "
                "last_sleep_at = COALESCE(?, last_sleep_at), "
                "last_wake_at = COALESCE(?, last_wake_at) "
                "WHERE session_id = ? AND ended_at IS NULL",
                (
                    heartbeat_at,
                    last_scan_at,
                    last_window_activation_at,
                    last_sleep_at,
                    last_wake_at,
                    session_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError(session_id)

    def end_runtime_session(
        self,
        session_id: str,
        ended_at: str,
        *,
        exit_reason: str,
        graceful_exit: bool,
    ) -> None:
        self.initialize()
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE runtime_sessions SET ended_at = ?, exit_reason = ?, graceful_exit = ? "
                "WHERE session_id = ? AND ended_at IS NULL",
                (ended_at, exit_reason, int(graceful_exit), session_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(session_id)

    def get_runtime_session(self, session_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT session_id, pid, ppid, app_path, source_commit, started_at, "
                "last_heartbeat_at, ended_at, exit_reason, graceful_exit, last_scan_at, "
                "last_window_activation_at, last_sleep_at, last_wake_at, "
                "previous_session_id, previous_unclean_exit, watchdog_restart_count "
                "FROM runtime_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        keys = (
            "session_id", "pid", "ppid", "app_path", "source_commit", "started_at",
            "last_heartbeat_at", "ended_at", "exit_reason", "graceful_exit",
            "last_scan_at", "last_window_activation_at", "last_sleep_at", "last_wake_at",
            "previous_session_id", "previous_unclean_exit", "watchdog_restart_count",
        )
        return dict(zip(keys, row))

    def list_runtime_sessions(self, trade_date: str | None = None) -> list[dict[str, Any]]:
        self.initialize()
        query = (
            "SELECT session_id, pid, ppid, app_path, source_commit, started_at, "
            "last_heartbeat_at, ended_at, exit_reason, graceful_exit, last_scan_at, "
            "last_window_activation_at FROM runtime_sessions"
        )
        values: tuple[Any, ...] = ()
        if trade_date is not None:
            query += " WHERE substr(started_at, 1, 10) = ?"
            values = (trade_date,)
        query += " ORDER BY started_at, session_id"
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        keys = (
            "session_id", "pid", "ppid", "app_path", "source_commit", "started_at",
            "last_heartbeat_at", "ended_at", "exit_reason", "graceful_exit",
            "last_scan_at", "last_window_activation_at", "last_sleep_at", "last_wake_at",
            "previous_session_id", "previous_unclean_exit", "watchdog_restart_count",
        )
        return [dict(zip(keys, row)) for row in rows]

    def start_scan_attempt(
        self,
        *,
        attempt_id: str,
        session_id: str,
        started_at: str,
        operation: str,
        thread_name: str,
        timer_active: bool,
        detail: str = "scan started",
    ) -> None:
        self.initialize()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO scan_attempts "
                "(attempt_id, session_id, started_at, last_heartbeat_at, completed_at, state, "
                "operation, thread_name, timer_active, detail, recovery_count) "
                "VALUES (?, ?, ?, ?, NULL, 'running', ?, ?, ?, ?, 0)",
                (attempt_id, session_id, started_at, started_at, operation, thread_name,
                 int(timer_active), detail),
            )

    def heartbeat_scan_attempt(
        self,
        attempt_id: str,
        heartbeat_at: str,
        *,
        detail: str | None = None,
        timer_active: bool | None = None,
    ) -> None:
        self.initialize()
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE scan_attempts SET last_heartbeat_at = ?, "
                "detail = COALESCE(?, detail), timer_active = COALESCE(?, timer_active) "
                "WHERE attempt_id = ? AND completed_at IS NULL",
                (
                    heartbeat_at,
                    detail,
                    None if timer_active is None else int(timer_active),
                    attempt_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError(attempt_id)

    def finish_scan_attempt(
        self,
        attempt_id: str,
        completed_at: str,
        *,
        state: str,
        detail: str,
        recovery_count: int = 0,
    ) -> None:
        self.initialize()
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE scan_attempts SET completed_at = ?, last_heartbeat_at = ?, state = ?, "
                "detail = ?, recovery_count = ? WHERE attempt_id = ? AND completed_at IS NULL",
                (completed_at, completed_at, state, detail, recovery_count, attempt_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(attempt_id)

    def list_scan_attempts(
        self,
        *,
        session_id: str | None = None,
        trade_date: str | None = None,
    ) -> list[dict[str, Any]]:
        self.initialize()
        query = (
            "SELECT attempt_id, session_id, started_at, last_heartbeat_at, completed_at, state, "
            "operation, thread_name, timer_active, detail, recovery_count FROM scan_attempts"
        )
        clauses: list[str] = []
        values: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            values.append(session_id)
        if trade_date is not None:
            clauses.append("substr(started_at, 1, 10) = ?")
            values.append(trade_date)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY started_at, attempt_id"
        with self.connect() as connection:
            rows = connection.execute(query, tuple(values)).fetchall()
        keys = (
            "attempt_id", "session_id", "started_at", "last_heartbeat_at", "completed_at",
            "state", "operation", "thread_name", "timer_active", "detail", "recovery_count",
        )
        return [dict(zip(keys, row)) for row in rows]

    def record_scan_recovery_event(
        self,
        *,
        session_id: str,
        occurred_at: str,
        state: str,
        detail: str,
    ) -> None:
        """Keep recovery evidence in the credential-free notes table."""
        self.put_note(
            f"scan-recovery:{session_id}:{occurred_at}",
            json.dumps({"occurred_at": occurred_at, "state": state, "detail": detail},
                       ensure_ascii=False, sort_keys=True),
        )

    def list_scan_recovery_events(self, session_id: str | None = None) -> list[dict[str, Any]]:
        self.initialize()
        prefix = f"scan-recovery:{session_id}:" if session_id else "scan-recovery:"
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT key, value FROM notes WHERE key LIKE ? ORDER BY key",
                (prefix + "%",),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for key, value in rows:
            try:
                payload = json.loads(str(value))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                output.append({"key": key, **payload})
        return output

    def put_note(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO notes VALUES (?, ?)", (key, value))

    def get_note(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM notes WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def record_config_version(self, version: str, source: str, settings_json: str) -> None:
        self.initialize()
        with self.connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO config_versions VALUES (?, ?, ?, ?)",
                    (version, source, settings_json, datetime.now().isoformat()),
                )
            except sqlite3.IntegrityError as error:
                raise FileExistsError(f"config version already exists: {version}") from error

    def record_snapshot(self, payload_json: str, metadata: dict[str, str | int | bool]) -> int:
        self.initialize()
        required = {
            "source_ts",
            "generated_at",
            "health",
            "provider_version",
            "config_version",
            "app_version",
        }
        if missing := required - metadata.keys():
            raise ValueError(f"snapshot metadata missing: {sorted(missing)}")
        with self.connect() as connection:
            return self._insert_snapshot(connection, payload_json, metadata)

    @staticmethod
    def _insert_snapshot(
        connection: sqlite3.Connection,
        payload_json: str,
        metadata: dict[str, str | int | bool],
    ) -> int:
        cursor = connection.execute(
                "INSERT INTO candidate_snapshots "
                "(source_ts, generated_at, health, overall_weak, provider_version, config_version, "
                "app_version, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(metadata["source_ts"]),
                    str(metadata["generated_at"]),
                    str(metadata["health"]),
                    int(bool(metadata.get("overall_weak", False))),
                    str(metadata["provider_version"]),
                    str(metadata["config_version"]),
                    str(metadata["app_version"]),
                    payload_json,
                ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("snapshot insert did not return an id")
        return int(cursor.lastrowid)

    def record_batch(self, batch: CandidateBatch) -> int:
        """Store the complete deterministic result, including reasons and sub-scores."""
        first = batch.candidates[0] if batch.candidates else None
        if first is None:
            raise ValueError("empty candidate batches are not persisted as new snapshots")
        self.initialize()
        with self.connect() as connection:
            with connection:
                return self.record_batch_in(connection, batch)

    def record_batch_in(
        self,
        connection: sqlite3.Connection,
        batch: CandidateBatch,
    ) -> int:
        """Persist one batch inside the caller's open transaction."""
        first = batch.candidates[0] if batch.candidates else None
        if first is None:
            raise ValueError("empty candidate batches are not persisted as new snapshots")
        metadata: dict[str, str | int | bool] = {
            "source_ts": batch.source_ts.isoformat(),
            "generated_at": batch.generated_at.isoformat(),
            "health": batch.health.value,
            "overall_weak": batch.overall_weak,
            "provider_version": first.provider_version,
            "config_version": first.config_version,
            "app_version": first.app_version,
        }
        snapshot_id = self._insert_snapshot(
            connection,
            batch.trace_payload(),
            metadata,
        )
        for rank, candidate in enumerate(batch.candidates, start=1):
            connection.execute(
                "INSERT INTO candidate_items "
                "(snapshot_id, rank, code, name, level, is_formal, is_supplement, "
                "price, change_pct, sector_code, sector_name, fund_label, explanation, "
                "payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot_id,
                    rank,
                    candidate.code,
                    candidate.name,
                    candidate.level,
                    int(candidate.is_formal),
                    int(candidate.is_supplement),
                    candidate.price,
                    candidate.change_pct,
                    candidate.sector_code,
                    candidate.sector,
                    candidate.fund_label,
                    "；".join(candidate.reasons[:5]),
                    json.dumps(
                        asdict(candidate),
                        ensure_ascii=False,
                        default=str,
                        sort_keys=True,
                    ),
                ),
            )
        return snapshot_id

    def get_app_setting(self, key: str) -> Any:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    def set_app_setting(self, key: str, value: Any) -> None:
        self.initialize()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO app_settings (key, value_json, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, "
                "updated_at = excluded.updated_at",
                (
                    key,
                    json.dumps(value, ensure_ascii=False, sort_keys=True),
                    datetime.now().isoformat(),
                ),
            )

    def record_alert_event(
        self,
        snapshot_id: int,
        displayed_at: str,
        decision: str,
        channel: str,
        trigger_type: str = "intraday",
        detail: dict[str, Any] | None = None,
    ) -> int:
        """Persist one alert event and return its id."""
        if any(word in channel.lower() for word in ("token", "secret", "password", "account")):
            raise ValueError("alert channel must not contain credentials or account information")
        self.initialize()
        with self.connect() as connection:
            with connection:
                return self.record_alert_event_in(
                    connection,
                    snapshot_id=snapshot_id,
                    displayed_at=displayed_at,
                    decision=decision,
                    channel=channel,
                    trigger_type=trigger_type,
                    detail=detail,
                )

    @staticmethod
    def record_alert_event_in(
        connection: sqlite3.Connection,
        *,
        snapshot_id: int,
        displayed_at: str,
        decision: str,
        channel: str,
        trigger_type: str = "intraday",
        detail: dict[str, Any] | None = None,
    ) -> int:
        """Insert an alert event inside the caller's open transaction."""
        if any(word in channel.lower() for word in ("token", "secret", "password", "account")):
            raise ValueError("alert channel must not contain credentials or account information")
        cursor = connection.execute(
            "INSERT INTO alert_events "
            "(snapshot_id, displayed_at, decision, channel, trigger_type, detail_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                snapshot_id,
                displayed_at,
                decision,
                channel,
                trigger_type,
                json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
            ),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("alert event insert did not return an id")
        return int(cursor.lastrowid)

    def record_health_metric(self, metadata: dict[str, str]) -> None:
        self.initialize()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO health_metrics "
                "(source_ts, received_ts, state, provider_version, config_version, detail) "
                "VALUES (:source_ts, :received_ts, :state, :provider_version, "
                ":config_version, :detail)",
                metadata,
            )

    def count_health_interruptions(self, trade_date: str) -> int:
        """Count persisted interruption onsets for one local trading date."""
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM health_metrics "
                "WHERE substr(received_ts, 1, 10) = ? "
                "AND state IN ('WARMING', 'STALE', 'STOPPED')",
                (trade_date,),
            ).fetchone()
        return 0 if row is None else int(row[0])

    def ensure_automation_task(self, task: dict[str, str]) -> dict[str, Any]:
        """Create one durable daily obligation without resetting an existing state."""
        required = {
            "task_key",
            "task_type",
            "trade_date",
            "target_at",
            "deadline_at",
            "state",
            "updated_at",
            "detail",
        }
        if missing := required - task.keys():
            raise ValueError(f"automation task missing: {sorted(missing)}")
        self.initialize()
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO automation_tasks "
                "(task_key, task_type, trade_date, target_at, deadline_at, state, "
                "attempts, updated_at, detail, snapshot_id) "
                "VALUES (:task_key, :task_type, :trade_date, :target_at, :deadline_at, "
                ":state, 0, :updated_at, :detail, NULL)",
                task,
            )
        saved = self.get_automation_task(str(task["task_key"]))
        if saved is None:
            raise RuntimeError("automation task insert was not readable")
        return saved

    def update_automation_task(
        self,
        task_key: str,
        *,
        state: str,
        updated_at: str,
        detail: str,
        snapshot_id: int | None = None,
        increment_attempt: bool = False,
    ) -> None:
        self.initialize()
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE automation_tasks SET state = ?, updated_at = ?, detail = ?, "
                "snapshot_id = COALESCE(?, snapshot_id), "
                "attempts = attempts + ? WHERE task_key = ?",
                (
                    state,
                    updated_at,
                    detail,
                    snapshot_id,
                    int(increment_attempt),
                    task_key,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError(task_key)

    def get_automation_task(self, task_key: str) -> dict[str, Any] | None:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT task_key, task_type, trade_date, target_at, deadline_at, state, "
                "attempts, updated_at, detail, snapshot_id "
                "FROM automation_tasks WHERE task_key = ?",
                (task_key,),
            ).fetchone()
        if row is None:
            return None
        keys = (
            "task_key",
            "task_type",
            "trade_date",
            "target_at",
            "deadline_at",
            "state",
            "attempts",
            "updated_at",
            "detail",
            "snapshot_id",
        )
        return dict(zip(keys, row))

    def list_automation_tasks(self, trade_date: str) -> list[dict[str, Any]]:
        self.initialize()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT task_key, task_type, trade_date, target_at, deadline_at, state, "
                "attempts, updated_at, detail, snapshot_id "
                "FROM automation_tasks WHERE trade_date = ? ORDER BY target_at",
                (trade_date,),
            ).fetchall()
        keys = (
            "task_key",
            "task_type",
            "trade_date",
            "target_at",
            "deadline_at",
            "state",
            "attempts",
            "updated_at",
            "detail",
            "snapshot_id",
        )
        return [dict(zip(keys, row)) for row in rows]

    def record_scan_run(self, record: dict[str, Any]) -> int:
        required = {
            "started_at",
            "completed_at",
            "trigger_type",
            "health",
            "detail",
            "audit_json",
        }
        if missing := required - record.keys():
            raise ValueError(f"scan run missing: {sorted(missing)}")
        self.initialize()
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO scan_runs "
                "(started_at, completed_at, trigger_type, task_key, health, source_ts, "
                "coverage_ratio, elapsed_seconds, source_age_seconds, detail, "
                "raw_batch_json, stable_batch_json, audit_json) "
                "VALUES (:started_at, :completed_at, :trigger_type, :task_key, :health, "
                ":source_ts, :coverage_ratio, :elapsed_seconds, :source_age_seconds, "
                ":detail, :raw_batch_json, :stable_batch_json, :audit_json)",
                {
                    "task_key": None,
                    "source_ts": None,
                    "coverage_ratio": None,
                    "elapsed_seconds": None,
                    "source_age_seconds": None,
                    "raw_batch_json": None,
                    "stable_batch_json": None,
                    **record,
                },
            )
        if cursor.lastrowid is None:
            raise RuntimeError("scan run insert did not return an id")
        return int(cursor.lastrowid)

    def list_scan_runs(self, trade_date: str) -> list[dict[str, Any]]:
        self.initialize()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, started_at, completed_at, trigger_type, task_key, health, "
                "source_ts, coverage_ratio, elapsed_seconds, source_age_seconds, detail, "
                "raw_batch_json, stable_batch_json, audit_json "
                "FROM scan_runs WHERE substr(completed_at, 1, 10) = ? "
                "ORDER BY completed_at, id",
                (trade_date,),
            ).fetchall()
        keys = (
            "id",
            "started_at",
            "completed_at",
            "trigger_type",
            "task_key",
            "health",
            "source_ts",
            "coverage_ratio",
            "elapsed_seconds",
            "source_age_seconds",
            "detail",
            "raw_batch_json",
            "stable_batch_json",
            "audit_json",
        )
        return [dict(zip(keys, row)) for row in rows]

    def has_scan_activity(self, trade_date: str) -> bool:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM scan_runs WHERE substr(completed_at, 1, 10) = ? "
                "AND health = 'HEALTHY' LIMIT 1",
                (trade_date,),
            ).fetchone()
        return row is not None

    def apply_transaction(self, statements: list[tuple[str, tuple[Any, ...]]]) -> None:
        self.initialize()
        with self.connect() as connection:
            with connection:
                for statement, values in statements:
                    connection.execute(statement, values)

    def backup(self, destination: Path) -> Path:
        """Create a validated, atomically replaced, durable SQLite snapshot."""
        self.initialize()
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.resolve() == self.path.resolve():
            raise ValueError("backup destination must differ from the live database")
        staging = destination.with_name(f".{destination.name}.tmp")
        self._remove_database_family(staging)
        with self.connect() as source, closing(sqlite3.connect(staging)) as target:
            target.execute("PRAGMA journal_mode=DELETE")
            target.execute("PRAGMA synchronous=FULL")
            source.backup(target)
            target.commit()
        self._validate_database_file(staging)
        self._fsync_file(staging)
        self._remove_database_sidecars(destination)
        staging.replace(destination)
        self._fsync_directory(destination.parent)
        return destination

    def close(self, *, checkpoint: bool = True) -> None:
        """Flush and close this process's persistent SQLite connection."""
        if self.read_only:
            return
        connection = getattr(self._thread_local, "connection", None)
        if connection is None:
            return
        try:
            if checkpoint:
                try:
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.DatabaseError:
                    # Another process may be using the WAL.  Closing the
                    # durable connection still releases this process cleanly;
                    # the next startup will validate the database family.
                    pass
        finally:
            connection.close()
            del self._thread_local.connection
            self._wal_configured = False
            self._integrity_verified_version = None

    def rollback(self, backup: Path) -> None:
        if not backup.exists():
            raise FileNotFoundError(backup)
        staging = self.path.with_name(f"{self.path.name}.restore-tmp")
        previous = self.path.with_name(f"{self.path.name}.restore-old")
        self._remove_database_family(staging)
        shutil.copy2(backup, staging)
        self._validate_database_file(staging)

        # A restore is a controlled offline operation. Replacing the main file
        # while old WAL/SHM sidecars survive can replay pages from the damaged
        # database into the restored snapshot when Web or Worker starts again.
        self._close_thread_connection()
        current_family_exists = self.path.exists() or any(
            sidecar.exists() for sidecar in self._database_sidecars(self.path)
        )
        if current_family_exists:
            self._remove_database_family(previous)
            self._move_database_family(self.path, previous)
        try:
            staging.replace(self.path)
            self.read_only = False
            self._validate_database_file(self.path)
        except BaseException:
            self._remove_database_family(self.path)
            self._move_database_family(previous, self.path)
            raise
        finally:
            self._remove_database_family(staging)

    def list_recent_snapshots(self, limit: int = 20) -> list[dict[str, str | int]]:
        """Read visible candidate batches without initializing or mutating storage.

        The desktop history view uses this method from a worker thread.  It is
        intentionally query-only: an absent/corrupt database is surfaced to
        the caller instead of being silently created or migrated by the UI.
        """
        if limit < 1:
            raise ValueError("limit must be at least one")
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, source_ts, generated_at, health, overall_weak, "
                "provider_version, config_version, app_version, payload_json "
                "FROM candidate_snapshots ORDER BY source_ts DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        keys = (
            "id",
            "source_ts",
            "generated_at",
            "health",
            "overall_weak",
            "provider_version",
            "config_version",
            "app_version",
            "payload_json",
        )
        return [dict(zip(keys, row)) for row in rows]

    def list_alert_history(
        self,
        *,
        now: datetime,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        if days < 1:
            raise ValueError("history days must be at least one")
        cutoff = (now - timedelta(days=days)).isoformat()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT e.id, e.displayed_at, e.decision, e.channel, e.trigger_type, "
                "e.detail_json, "
                "s.id, s.source_ts, s.overall_weak, s.payload_json "
                "FROM alert_events e JOIN candidate_snapshots s ON s.id = e.snapshot_id "
                "WHERE e.displayed_at >= ? "
                "ORDER BY e.displayed_at DESC, e.id DESC",
                (cutoff,),
            ).fetchall()
        keys = (
            "alert_id",
            "displayed_at",
            "decision",
            "channel",
            "trigger_type",
            "detail_json",
            "snapshot_id",
            "source_ts",
            "overall_weak",
            "payload_json",
        )
        return [dict(zip(keys, row)) for row in rows]

    def create_candidate_outcomes(self, entries: list[dict[str, Any]]) -> int:
        """Insert outcome rows idempotently in one short transaction."""
        if not entries:
            return 0
        self._validate_candidate_outcome_entries(entries)
        with self.transaction() as connection:
            return self.create_candidate_outcomes_in(connection, entries)

    @staticmethod
    def _validate_candidate_outcome_entries(entries: list[dict[str, Any]]) -> None:
        required = {
            "entry_snapshot_id",
            "entry_alert_id",
            "entry_trade_date",
            "slot",
            "rank",
            "code",
            "name",
            "entry_price",
            "entry_source_ts",
            "target_slot",
            "quality",
            "provider_version",
            "config_version",
            "app_version",
            "created_at",
            "updated_at",
        }
        for entry in entries:
            if missing := required - entry.keys():
                raise ValueError(f"candidate outcome missing: {sorted(missing)}")
            if str(entry["slot"]) not in {"09:45", "14:45"}:
                raise ValueError("candidate outcome slot is invalid")
            if int(entry["rank"]) not in {1, 2, 3}:
                raise ValueError("candidate outcome rank is invalid")
            if float(entry["entry_price"]) <= 0:
                raise ValueError("candidate outcome entry price must be positive")

    def create_candidate_outcomes_in(
        self,
        connection: sqlite3.Connection,
        entries: list[dict[str, Any]],
    ) -> int:
        """Insert outcome rows inside an existing business transaction."""
        if not entries:
            return 0
        self._validate_candidate_outcome_entries(entries)
        inserted = 0
        for entry in entries:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO candidate_outcomes "
                "(entry_snapshot_id, entry_alert_id, entry_trade_date, slot, rank, "
                "code, name, entry_price, entry_source_ts, target_trade_date, "
                "target_slot, exit_price, exit_source_ts, return_pct, status, outcome, "
                "settlement_method, quality, provider_version, config_version, "
                "app_version, created_at, updated_at, safe_reason, settlement_attempts, "
                "last_attempt_at, next_retry_at) "
                "VALUES (:entry_snapshot_id, :entry_alert_id, :entry_trade_date, "
                ":slot, :rank, :code, :name, :entry_price, :entry_source_ts, "
                ":target_trade_date, :target_slot, NULL, NULL, NULL, :status, NULL, "
                "NULL, :quality, :provider_version, :config_version, :app_version, "
                ":created_at, :updated_at, :safe_reason, 0, NULL, :next_retry_at)",
                {
                    **entry,
                    "target_trade_date": entry.get("target_trade_date"),
                    "status": entry.get("status", "pending"),
                    "safe_reason": entry.get("safe_reason"),
                    "next_retry_at": entry.get("next_retry_at"),
                },
            )
            inserted += max(0, cursor.rowcount)
        return inserted

    def assign_candidate_outcome_target(
        self,
        outcome_id: int,
        *,
        target_trade_date: str,
        next_retry_at: str,
        updated_at: str,
    ) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE candidate_outcomes SET target_trade_date = ?, next_retry_at = ?, "
                "updated_at = ?, safe_reason = NULL WHERE id = ? AND status = 'pending'",
                (target_trade_date, next_retry_at, updated_at, outcome_id),
            )
        return cursor.rowcount == 1

    def list_pending_candidate_outcomes(
        self,
        *,
        target_trade_date: str | None = None,
        target_slot: str | None = None,
        entry_snapshot_id: int | None = None,
        unresolved_only: bool = False,
        newest_first: bool = False,
        limit: int | None = 100,
    ) -> list[dict[str, Any]]:
        if limit is not None and limit < 1:
            raise ValueError("candidate outcome limit must be positive")
        where = ["status = 'pending'"]
        values: list[Any] = []
        if unresolved_only:
            where.append("target_trade_date IS NULL")
        elif target_trade_date is not None:
            where.append("target_trade_date = ?")
            values.append(target_trade_date)
        if target_slot is not None:
            if target_slot not in {"09:45", "14:45"}:
                raise ValueError("candidate outcome target slot is invalid")
            where.append("target_slot = ?")
            values.append(target_slot)
        if entry_snapshot_id is not None:
            if entry_snapshot_id < 1:
                raise ValueError("candidate outcome snapshot id must be positive")
            where.append("entry_snapshot_id = ?")
            values.append(entry_snapshot_id)
        columns = ", ".join(self._OUTCOME_COLUMNS)
        ordering = (
            "target_trade_date DESC, target_slot DESC, rank, id"
            if newest_first
            else "entry_trade_date, slot, rank, id"
        )
        limit_clause = ""
        if limit is not None:
            values.append(limit)
            limit_clause = " LIMIT ?"
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT {columns} FROM candidate_outcomes WHERE {' AND '.join(where)} "
                f"ORDER BY {ordering}{limit_clause}",
                tuple(values),
            ).fetchall()
        return [dict(zip(self._OUTCOME_COLUMNS, row)) for row in rows]

    def settle_candidate_outcome(
        self,
        outcome_id: int,
        *,
        exit_price: float,
        exit_source_ts: str,
        return_pct: float,
        outcome: str,
        settlement_method: str,
        quality: str,
        updated_at: str,
    ) -> bool:
        if exit_price <= 0:
            raise ValueError("candidate outcome exit price must be positive")
        if outcome not in {"win", "loss", "flat"}:
            raise ValueError("candidate outcome result is invalid")
        if settlement_method not in {
            "realtime_scan",
            "realtime_batch",
            "historical_minute",
        }:
            raise ValueError("candidate outcome settlement method is invalid")
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE candidate_outcomes SET exit_price = ?, exit_source_ts = ?, "
                "return_pct = ?, status = 'settled', outcome = ?, settlement_method = ?, "
                "quality = ?, updated_at = ?, safe_reason = NULL, next_retry_at = NULL "
                "WHERE id = ? AND status = 'pending'",
                (
                    exit_price,
                    exit_source_ts,
                    return_pct,
                    outcome,
                    settlement_method,
                    quality,
                    updated_at,
                    outcome_id,
                ),
            )
        return cursor.rowcount == 1

    def mark_candidate_outcome_unavailable(
        self,
        outcome_id: int,
        *,
        quality: str,
        safe_reason: str,
        updated_at: str,
    ) -> bool:
        if not safe_reason.strip():
            raise ValueError("candidate outcome safe reason must not be empty")
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE candidate_outcomes SET status = 'unavailable', outcome = NULL, "
                "settlement_method = NULL, quality = ?, updated_at = ?, safe_reason = ?, "
                "next_retry_at = NULL WHERE id = ? AND status = 'pending'",
                (quality, updated_at, safe_reason, outcome_id),
            )
        return cursor.rowcount == 1

    def record_candidate_outcome_attempt(
        self,
        outcome_id: int,
        *,
        attempted_at: str,
        next_retry_at: str | None,
    ) -> bool:
        """Persist one bounded attempt before performing its network request."""
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE candidate_outcomes SET settlement_attempts = settlement_attempts + 1, "
                "last_attempt_at = ?, next_retry_at = ?, updated_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (attempted_at, next_retry_at, attempted_at, outcome_id),
            )
        return cursor.rowcount == 1

    def defer_candidate_outcome(
        self,
        outcome_id: int,
        *,
        safe_reason: str,
        next_retry_at: str | None,
        updated_at: str,
    ) -> bool:
        """Keep a failed or not-yet-published minute pending for a bounded retry."""
        if not safe_reason.strip():
            raise ValueError("candidate outcome safe reason must not be empty")
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE candidate_outcomes SET quality = 'UNVERIFIED', safe_reason = ?, "
                "next_retry_at = ?, updated_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (safe_reason, next_retry_at, updated_at, outcome_id),
            )
        return cursor.rowcount == 1

    def list_candidate_outcomes(
        self,
        *,
        trading_days: int | None,
    ) -> list[dict[str, Any]]:
        if trading_days is not None and trading_days < 1:
            raise ValueError("candidate outcome trading days must be positive")
        where = ""
        values: tuple[Any, ...] = ()
        if trading_days is not None:
            where = (
                "WHERE entry_trade_date IN ("
                "SELECT entry_trade_date FROM candidate_outcomes "
                "GROUP BY entry_trade_date ORDER BY entry_trade_date DESC LIMIT ?)"
            )
            values = (trading_days,)
        columns = ", ".join(self._OUTCOME_COLUMNS)
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT {columns} FROM candidate_outcomes {where} "
                "ORDER BY entry_trade_date DESC, slot, rank, id",
                values,
            ).fetchall()
        return [dict(zip(self._OUTCOME_COLUMNS, row)) for row in rows]

    def list_scheduled_candidate_entries(
        self,
        *,
        now: datetime,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Read only scheduled alert candidates for safe, idempotent backfill."""
        if days < 1:
            raise ValueError("candidate outcome backfill days must be positive")
        cutoff = (now - timedelta(days=days)).isoformat()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT e.id, e.trigger_type, e.displayed_at, s.id, s.source_ts, "
                "s.health, s.provider_version, s.config_version, s.app_version, "
                "i.rank, i.code, i.name, i.price, i.payload_json, "
                "COUNT(*) OVER (PARTITION BY e.id) "
                "FROM alert_events e "
                "JOIN candidate_snapshots s ON s.id = e.snapshot_id "
                "JOIN candidate_items i ON i.snapshot_id = s.id "
                "WHERE e.displayed_at >= ? AND e.trigger_type IN "
                "('scheduled-09:45', 'scheduled-14:45') "
                "ORDER BY e.displayed_at, e.id, i.rank",
                (cutoff,),
            ).fetchall()
        keys = (
            "entry_alert_id",
            "trigger_type",
            "displayed_at",
            "entry_snapshot_id",
            "snapshot_source_ts",
            "health",
            "provider_version",
            "config_version",
            "app_version",
            "rank",
            "code",
            "name",
            "entry_price",
            "candidate_payload_json",
            "candidate_count",
        )
        return [dict(zip(keys, row)) for row in rows]

    def prune_history(self, *, before: datetime) -> int:
        """Delete only data outside the approved retention window.

        Older builds removed *every* snapshot that was not referenced by an
        alert.  That accidentally erased same-day manual/automatic observations
        (and the audit evidence needed to explain why Top3 changed).  The
        retention boundary now applies to snapshots themselves: recent
        observations are kept even when they never produced a popup.
        """
        self.initialize()
        cutoff = before.isoformat()
        with self.transaction() as connection:
            alert_ids = [
                int(row[0])
                for row in connection.execute(
                    "SELECT id FROM alert_events WHERE displayed_at < ?",
                    (cutoff,),
                )
            ]
            if alert_ids:
                connection.executemany(
                    "DELETE FROM alert_events WHERE id = ?",
                    ((alert_id,) for alert_id in alert_ids),
                )
            connection.execute(
                "DELETE FROM candidate_snapshots "
                "WHERE source_ts < ? AND id NOT IN "
                "(SELECT DISTINCT snapshot_id FROM alert_events)",
                (cutoff,),
            )
            connection.execute(
                "DELETE FROM scan_runs WHERE completed_at < ?",
                (cutoff,),
            )
            connection.execute(
                "DELETE FROM automation_tasks WHERE deadline_at < ? "
                "AND state IN ('succeeded', 'failed')",
                (cutoff,),
            )
        return len(alert_ids)

    def record_daily_summary(self, summary: dict[str, Any]) -> None:
        required = {
            "trade_date",
            "generated_at",
            "alert_count",
            "top_sectors",
            "repeated_candidates",
            "closing_performance",
            "fund_summary",
            "health_summary",
            "summary_text",
            "version",
        }
        if missing := required - summary.keys():
            raise ValueError(f"daily summary missing: {sorted(missing)}")
        self.initialize()
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO daily_summaries "
                "(trade_date, generated_at, alert_count, top_sectors_json, "
                "repeated_candidates_json, closing_performance_json, fund_summary, "
                "health_summary, summary_text, version, catch_up) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(summary["trade_date"]),
                    str(summary["generated_at"]),
                    int(summary["alert_count"]),
                    json.dumps(summary["top_sectors"], ensure_ascii=False, sort_keys=True),
                    json.dumps(
                        summary["repeated_candidates"],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    json.dumps(
                        summary["closing_performance"],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    str(summary["fund_summary"]),
                    str(summary["health_summary"]),
                    str(summary["summary_text"]),
                    str(summary["version"]),
                    int(summary.get("catch_up", 0)),
                ),
            )

    def get_daily_summary(self, trade_date: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT trade_date, generated_at, alert_count, top_sectors_json, "
                "repeated_candidates_json, closing_performance_json, fund_summary, "
                "health_summary, summary_text, version, catch_up "
                "FROM daily_summaries WHERE trade_date = ?",
                (trade_date,),
            ).fetchone()
        if row is None:
            return None
        return {
            "trade_date": row[0],
            "generated_at": row[1],
            "alert_count": row[2],
            "top_sectors": json.loads(row[3]),
            "repeated_candidates": json.loads(row[4]),
            "closing_performance": json.loads(row[5]),
            "fund_summary": row[6],
            "health_summary": row[7],
            "summary_text": row[8],
            "version": row[9],
            "catch_up": row[10],
        }

    def list_daily_summaries(self, *, since: date) -> list[dict[str, Any]]:
        """Return recent daily summaries newest first without changing storage."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT trade_date, generated_at, alert_count, top_sectors_json, "
                "repeated_candidates_json, closing_performance_json, fund_summary, "
                "health_summary, summary_text, version "
                "FROM daily_summaries WHERE trade_date >= ? "
                "ORDER BY trade_date DESC",
                (since.isoformat(),),
            ).fetchall()
        return [
            {
                "trade_date": row[0],
                "generated_at": row[1],
                "alert_count": row[2],
                "top_sectors": json.loads(row[3]),
                "repeated_candidates": json.loads(row[4]),
                "closing_performance": json.loads(row[5]),
                "fund_summary": row[6],
                "health_summary": row[7],
                "summary_text": row[8],
                "version": row[9],
            }
            for row in rows
        ]

    def prune_daily_summaries(self, *, before: date) -> int:
        """Delete summaries older than the Human Owner-approved 31-day window."""
        if self.read_only:
            raise RuntimeError("cannot prune daily summaries from a read-only store")
        self.initialize()
        with self.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM daily_summaries WHERE trade_date < ?",
                (before.isoformat(),),
            )
        return max(cursor.rowcount, 0)

    # ------------------------------------------------------------------
    # Schema v7 web projection queries (read-only, paginated, bounded).
    # ------------------------------------------------------------------

    def query_snapshots(
        self,
        *,
        limit: int,
        cursor: int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        code: str | None = None,
        repeat_active: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Page candidate snapshots newest-first with optional date/code filters.

        ``cursor`` is the last seen snapshot id; rows are returned strictly
        below it so clients can page without duplication.
        """
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        clauses: list[str] = []
        values: list[Any] = []
        if cursor is not None:
            clauses.append("s.id < ?")
            values.append(int(cursor))
        if from_date is not None:
            clauses.append("substr(s.source_ts, 1, 10) >= ?")
            values.append(from_date)
        if to_date is not None:
            clauses.append("substr(s.source_ts, 1, 10) <= ?")
            values.append(to_date)
        if code is not None:
            clauses.append(
                "s.id IN (SELECT DISTINCT snapshot_id FROM candidate_items "
                "WHERE code = ?)"
            )
            values.append(code)
        if repeat_active:
            clauses.append(
                "EXISTS ("
                "SELECT 1 FROM candidate_items AS i "
                "JOIN candidate_repeat_days AS d "
                "ON d.code = i.code AND d.trade_date = substr(s.source_ts, 1, 10) "
                "WHERE i.snapshot_id = s.id AND d.active_after = 1"
                ")"
            )
        query = (
            "SELECT s.id, s.source_ts, s.generated_at, s.health, s.overall_weak, "
            "s.provider_version, s.config_version, s.app_version, s.payload_json "
            "FROM candidate_snapshots s"
        )
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY s.id DESC LIMIT ?"
        values.append(limit)
        with self.connect() as connection:
            rows = connection.execute(query, tuple(values)).fetchall()
        return [
            {
                "id": row[0],
                "source_ts": row[1],
                "generated_at": row[2],
                "health": row[3],
                "overall_weak": row[4],
                "provider_version": row[5],
                "config_version": row[6],
                "app_version": row[7],
                "payload_json": row[8],
            }
            for row in rows
        ]

    def query_snapshot_items(self, snapshot_ids: Sequence[int]) -> dict[int, list[dict[str, Any]]]:
        """Load displayed Top3 rows for a page of snapshots, grouped by id."""
        if not snapshot_ids:
            return {}
        placeholders = ", ".join("?" for _ in snapshot_ids)
        query = (
            "SELECT snapshot_id, rank, code, name, level, is_formal, is_supplement, "
            "price, change_pct, sector_code, sector_name, fund_label, explanation "
            f"FROM candidate_items WHERE snapshot_id IN ({placeholders}) "
            "ORDER BY snapshot_id, rank"
        )
        with self.connect() as connection:
            rows = connection.execute(query, tuple(int(item) for item in snapshot_ids)).fetchall()
        grouped: dict[int, list[dict[str, Any]]] = {int(item): [] for item in snapshot_ids}
        keys = (
            "snapshot_id",
            "rank",
            "code",
            "name",
            "level",
            "is_formal",
            "is_supplement",
            "price",
            "change_pct",
            "sector_code",
            "sector_name",
            "fund_label",
            "explanation",
        )
        for row in rows:
            payload = dict(zip(keys, row))
            snapshot_id = int(payload.pop("snapshot_id"))
            grouped.setdefault(snapshot_id, []).append(payload)
        return grouped

    def get_snapshot_detail(self, snapshot_id: int, code: str) -> dict[str, Any] | None:
        """Return one immutable snapshot plus the requested candidate row."""
        with self.connect() as connection:
            snapshot = connection.execute(
                "SELECT id, source_ts, generated_at, health, overall_weak, "
                "provider_version, config_version, app_version, payload_json "
                "FROM candidate_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
            if snapshot is None:
                return None
            item = connection.execute(
                "SELECT rank, code, name, level, is_formal, is_supplement, price, "
                "change_pct, sector_code, sector_name, fund_label, explanation, "
                "payload_json FROM candidate_items "
                "WHERE snapshot_id = ? AND code = ?",
                (snapshot_id, code),
            ).fetchone()
        keys = (
            "id",
            "source_ts",
            "generated_at",
            "health",
            "overall_weak",
            "provider_version",
            "config_version",
            "app_version",
            "payload_json",
        )
        output: dict[str, Any] = dict(zip(keys, snapshot))
        output["candidate"] = None
        if item is not None:
            item_keys = (
                "rank",
                "code",
                "name",
                "level",
                "is_formal",
                "is_supplement",
                "price",
                "change_pct",
                "sector_code",
                "sector_name",
                "fund_label",
                "explanation",
                "payload_json",
            )
            output["candidate"] = dict(zip(item_keys, item))
        return output

    def query_alert_events(
        self,
        *,
        limit: int,
        cursor: int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        trigger_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Page alert history newest-first; joined with its immutable snapshot."""
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        clauses: list[str] = []
        values: list[Any] = []
        if cursor is not None:
            clauses.append("e.id < ?")
            values.append(int(cursor))
        if from_date is not None:
            clauses.append("substr(e.displayed_at, 1, 10) >= ?")
            values.append(from_date)
        if to_date is not None:
            clauses.append("substr(e.displayed_at, 1, 10) <= ?")
            values.append(to_date)
        if trigger_type is not None:
            clauses.append("e.trigger_type = ?")
            values.append(trigger_type)
        query = (
            "SELECT e.id, e.snapshot_id, e.displayed_at, e.decision, e.channel, "
            "e.trigger_type, e.detail_json, s.source_ts, s.overall_weak, s.payload_json "
            "FROM alert_events e JOIN candidate_snapshots s ON s.id = e.snapshot_id"
        )
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY e.id DESC LIMIT ?"
        values.append(limit)
        with self.connect() as connection:
            rows = connection.execute(query, tuple(values)).fetchall()
        return [
            {
                "alert_id": row[0],
                "snapshot_id": row[1],
                "displayed_at": row[2],
                "decision": row[3],
                "channel": row[4],
                "trigger_type": row[5],
                "detail_json": row[6],
                "source_ts": row[7],
                "overall_weak": row[8],
                "payload_json": row[9],
            }
            for row in rows
        ]

    def get_scan_run(self, scan_id: int) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, started_at, completed_at, trigger_type, task_key, health, "
                "source_ts, coverage_ratio, elapsed_seconds, source_age_seconds, detail, "
                "raw_batch_json, stable_batch_json, audit_json "
                "FROM scan_runs WHERE id = ?",
                (scan_id,),
            ).fetchone()
        if row is None:
            return None
        keys = (
            "id",
            "started_at",
            "completed_at",
            "trigger_type",
            "task_key",
            "health",
            "source_ts",
            "coverage_ratio",
            "elapsed_seconds",
            "source_age_seconds",
            "detail",
            "raw_batch_json",
            "stable_batch_json",
            "audit_json",
        )
        return dict(zip(keys, row))

    def read_public_state(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT state_version, snapshot_id, source_ts, updated_at, payload_json "
                "FROM web_public_state WHERE state_key = 'current'"
            ).fetchone()
        if row is None:
            return None
        return {
            "state_version": row[0],
            "snapshot_id": row[1],
            "source_ts": row[2],
            "updated_at": row[3],
            "payload_json": row[4],
        }

    def read_latest_snapshot_state(self) -> dict[str, Any] | None:
        """Read the newest persisted realtime snapshot and its three rows.

        The worker may publish a transient empty public projection while it is
        warming or restarting.  The dashboard must still be able to show the
        last immutable realtime observation until a newer snapshot replaces it.
        This query is deliberately read-only and does not initialize storage.
        """
        with self.connect() as connection:
            snapshot = connection.execute(
                "SELECT s.id, s.source_ts, s.generated_at, s.health, "
                "s.overall_weak, s.payload_json "
                "FROM candidate_snapshots AS s "
                "WHERE EXISTS ("
                "SELECT 1 FROM candidate_items AS i WHERE i.snapshot_id = s.id"
                ") ORDER BY s.source_ts DESC, s.id DESC LIMIT 1"
            ).fetchone()
            if snapshot is None:
                return None
            rows = connection.execute(
                "SELECT rank, code, name, level, is_formal, is_supplement, "
                "price, change_pct, sector_code, sector_name, fund_label, "
                "explanation, payload_json FROM candidate_items "
                "WHERE snapshot_id = ? ORDER BY rank",
                (snapshot[0],),
            ).fetchall()

        source_ts = str(snapshot[1])
        candidates: list[dict[str, Any]] = []
        for row in rows:
            item_payload: dict[str, Any] = {}
            try:
                parsed = json.loads(str(row[12]))
                if isinstance(parsed, dict):
                    item_payload = parsed
            except json.JSONDecodeError:
                pass
            candidates.append(
                {
                    "rank": int(row[0]),
                    "code": str(row[1]),
                    "name": str(row[2]),
                    "level": str(row[3]),
                    "is_formal": bool(row[4]),
                    "is_supplement": bool(row[5]),
                    "price": row[6],
                    "change_pct": row[7],
                    "sector_code": str(row[8]),
                    "sector_name": str(row[9]),
                    "sector_type": str(item_payload.get("sector_type", "industry")),
                    "fund_label": str(row[10]),
                    "explanation": str(row[11]),
                    "total_score": item_payload.get("total_score", 0.0),
                    "source_ts": source_ts,
                }
            )

        snapshot_payload: dict[str, Any] = {}
        try:
            parsed_snapshot = json.loads(str(snapshot[5]))
            if isinstance(parsed_snapshot, dict):
                snapshot_payload = parsed_snapshot
        except json.JSONDecodeError:
            pass
        return {
            "id": int(snapshot[0]),
            "source_ts": source_ts,
            "generated_at": snapshot[2],
            "health": snapshot[3],
            "overall_weak": bool(snapshot[4]),
            "candidates": candidates,
            "fund_module": snapshot_payload.get("fund_module", "unavailable"),
            "formal_count": snapshot_payload.get(
                "formal_count", sum(1 for candidate in candidates if candidate["is_formal"])
            ),
        }

    @staticmethod
    def upsert_public_state(
        connection: sqlite3.Connection,
        *,
        state_version: int,
        snapshot_id: int | None,
        source_ts: str | None,
        payload: dict[str, Any],
    ) -> None:
        """Write the shared dashboard projection inside the caller's transaction."""
        connection.execute(
            "INSERT INTO web_public_state "
            "(state_key, state_version, snapshot_id, source_ts, updated_at, payload_json) "
            "VALUES ('current', ?, ?, ?, ?, ?) "
            "ON CONFLICT(state_key) DO UPDATE SET "
            "state_version = excluded.state_version, "
            "snapshot_id = excluded.snapshot_id, "
            "source_ts = excluded.source_ts, "
            "updated_at = excluded.updated_at, "
            "payload_json = excluded.payload_json",
            (
                int(state_version),
                snapshot_id,
                source_ts,
                datetime.now().isoformat(),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
