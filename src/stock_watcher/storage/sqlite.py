from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stock_watcher.engine.candidates import CandidateBatch


@dataclass(slots=True)
class SQLiteStore:
    path: Path
    read_only: bool = False

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
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS notes (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
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
                    "config_version TEXT NOT NULL, "
                    "detail TEXT NOT NULL)"
                )
                if connection.execute("SELECT COUNT(*) FROM schema_version").fetchone() == (0,):
                    connection.execute("INSERT INTO schema_version VALUES (2, datetime('now'))")
                if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise RuntimeError("database integrity check failed")
        except (sqlite3.DatabaseError, RuntimeError):
            if self.path.exists():
                self.read_only = True
            raise

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
        return self.record_snapshot(
            batch.trace_payload(),
            {
                "source_ts": batch.source_ts.isoformat(),
                "generated_at": batch.generated_at.isoformat(),
                "health": batch.health.value,
                "overall_weak": batch.overall_weak,
                "provider_version": first.provider_version,
                "config_version": first.config_version,
                "app_version": first.app_version,
            },
        )

    def record_alert_event(
        self, snapshot_id: int, displayed_at: str, decision: str, channel: str
    ) -> None:
        if any(word in channel.lower() for word in ("token", "secret", "password", "account")):
            raise ValueError("alert channel must not contain credentials or account information")
        self.initialize()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO alert_events "
                "(snapshot_id, displayed_at, decision, channel) VALUES (?, ?, ?, ?)",
                (snapshot_id, displayed_at, decision, channel),
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
