from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class SQLiteStore:
    path: Path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS notes (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            if connection.execute("SELECT COUNT(*) FROM schema_version").fetchone() == (0,):
                connection.execute("INSERT INTO schema_version VALUES (1, datetime('now'))")

    def put_note(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO notes VALUES (?, ?)", (key, value))

    def get_note(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM notes WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def backup(self, destination: Path) -> Path:
        self.initialize()
        with self.connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
        return destination

    def rollback(self, backup: Path) -> None:
        if not backup.exists():
            raise FileNotFoundError(backup)
        # Use SQLite's backup API instead of copying a WAL database file: a
        # stale sidecar WAL could otherwise replay changes after the copy.
        with sqlite3.connect(backup) as source, self.connect() as target:
            source.backup(target)
        with self.connect() as connection:
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RuntimeError("rollback database failed integrity check")
