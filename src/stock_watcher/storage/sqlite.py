from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from stock_watcher.engine.candidates import CandidateBatch


@dataclass(slots=True)
class SQLiteStore:
    path: Path
    read_only: bool = False

    CURRENT_SCHEMA_VERSION: ClassVar[int] = 4

    def connect(self) -> sqlite3.Connection:
        connection = (
            sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            if self.read_only
            else sqlite3.connect(self.path)
        )
        if not self.read_only:
            connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        try:
            with self.connect() as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_version "
                    "(version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
                )
                self._assert_integrity(connection)
                version = self._schema_version(connection)
                if version < self.CURRENT_SCHEMA_VERSION:
                    self._backup_before_migration(version)
                    self._migrate_to_current(connection, version)
                self._assert_current_schema(connection)
        except (sqlite3.DatabaseError, RuntimeError):
            if self.path.exists():
                self.read_only = True
            raise

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
        with sqlite3.connect(self.path) as source, sqlite3.connect(backup) as target:
            source.backup(target)

    def _migrate_to_current(self, connection: sqlite3.Connection, version: int) -> None:
        if version not in (0, 1, 2, 3):
            raise RuntimeError(f"unsupported schema version: {version}")
        try:
            connection.execute("BEGIN IMMEDIATE")
            if version == 0:
                self._apply_v1_schema(connection)
            if version <= 1:
                self._apply_v2_migration(connection)
            if version <= 2:
                self._apply_v3_migration(connection)
            self._apply_v4_migration(connection)
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
    def _assert_integrity(connection: sqlite3.Connection) -> None:
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RuntimeError("database integrity check failed")

    def _assert_current_schema(self, connection: sqlite3.Connection) -> None:
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
        self._assert_integrity(connection)

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
        metadata: dict[str, str | int | bool] = {
            "source_ts": batch.source_ts.isoformat(),
            "generated_at": batch.generated_at.isoformat(),
            "health": batch.health.value,
            "overall_weak": batch.overall_weak,
            "provider_version": first.provider_version,
            "config_version": first.config_version,
            "app_version": first.app_version,
        }
        self.initialize()
        with self.connect() as connection:
            with connection:
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

    def record_alert_event(
        self,
        snapshot_id: int,
        displayed_at: str,
        decision: str,
        channel: str,
        trigger_type: str = "intraday",
    ) -> None:
        if any(word in channel.lower() for word in ("token", "secret", "password", "account")):
            raise ValueError("alert channel must not contain credentials or account information")
        self.initialize()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO alert_events "
                "(snapshot_id, displayed_at, decision, channel, trigger_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (snapshot_id, displayed_at, decision, channel, trigger_type),
            )

    def record_health_metric(self, metadata: dict[str, str]) -> None:
        self.initialize()
        with self.connect() as connection:
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
        with self.connect() as connection:
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
        with self.connect() as connection:
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
        with self.connect() as connection:
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
        self.initialize()
        with self.connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
        return destination

    def rollback(self, backup: Path) -> None:
        if not backup.exists():
            raise FileNotFoundError(backup)
        with sqlite3.connect(backup) as source, self.connect() as target:
            source.backup(target)
        with self.connect() as connection:
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RuntimeError("rollback database failed integrity check")

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
            "snapshot_id",
            "source_ts",
            "overall_weak",
            "payload_json",
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
        with self.connect() as connection:
            with connection:
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
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO daily_summaries "
                "(trade_date, generated_at, alert_count, top_sectors_json, "
                "repeated_candidates_json, closing_performance_json, fund_summary, "
                "health_summary, summary_text, version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                ),
            )

    def get_daily_summary(self, trade_date: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT trade_date, generated_at, alert_count, top_sectors_json, "
                "repeated_candidates_json, closing_performance_json, fund_summary, "
                "health_summary, summary_text, version "
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
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM daily_summaries WHERE trade_date < ?",
                (before.isoformat(),),
            )
        return max(cursor.rowcount, 0)
