"""Synthetic fixtures matching the inspected v10 columns; never production data."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from stock_watcher.feedback.schema import database, install_on_connection

NOW = datetime(2026, 9, 11, 7, 0, tzinfo=UTC)


def seed_snapshot(connection: sqlite3.Connection, snapshot_id: int = 1,
                  stamp: str = "2026-09-11T14:30:00+08:00") -> None:
    connection.execute(
        "INSERT INTO candidate_snapshots "
        "(id, source_ts, generated_at, health, overall_weak, provider_version, config_version, "
        "app_version, payload_json) VALUES (?, ?, ?, 'HEALTHY', 0, 'synthetic', 'test-v1', "
        "'0.7.0a3', '{}')", (snapshot_id, stamp, stamp),
    )
    for rank, (code, name, sector, pct) in enumerate((
        ("300829.SZ", "金丹科技", "生物制品", 6.82),
        ("300741.SZ", "华宝股份", "食品饮料", 4.15),
        ("300106.SZ", "西部牧业", "农产品加工", -1.24),
    ), 1):
        connection.execute(
            "INSERT INTO candidate_items "
            "(snapshot_id, rank, code, name, level, is_formal, is_supplement, price, "
            "change_pct, sector_code, sector_name, fund_label, explanation, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 18.46, ?, 'demo', ?, '资金未确认', '模拟原因', ?)",
            (snapshot_id, rank, code, name, ("强", "中", "近")[rank - 1], int(rank < 3),
             int(rank == 3), pct, sector,
             json.dumps({"total_score": 50 - rank, "price_score": 12,
                         "velocity_available": True, "secret": "must-not-be-copied"})),
        )
    connection.execute(
        "INSERT INTO web_public_state "
        "(state_key, state_version, snapshot_id, source_ts, updated_at, payload_json) "
        "VALUES ('current', ?, ?, ?, ?, '{}') "
        "ON CONFLICT(state_key) DO UPDATE SET snapshot_id=excluded.snapshot_id, "
        "source_ts=excluded.source_ts, updated_at=excluded.updated_at, "
        "state_version=excluded.state_version", (snapshot_id, snapshot_id, stamp, stamp),
    )


def synthetic_database(path: Path, *, extension: bool = True) -> Path:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE schema_version(version INTEGER NOT NULL, applied_at TEXT NOT NULL);
        INSERT INTO schema_version VALUES(10, 'fixture');
        CREATE TABLE web_users(user_id INTEGER PRIMARY KEY, active INTEGER NOT NULL, role TEXT);
        INSERT INTO web_users VALUES
          (1, 1, 'admin'), (2, 1, 'tester'), (3, 1, 'tester'), (4, 0, 'tester');
        CREATE TABLE candidate_snapshots(
          id INTEGER PRIMARY KEY, source_ts TEXT NOT NULL, generated_at TEXT NOT NULL,
          health TEXT NOT NULL, overall_weak INTEGER NOT NULL, provider_version TEXT NOT NULL,
          config_version TEXT NOT NULL, app_version TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TABLE candidate_items(
          id INTEGER PRIMARY KEY, snapshot_id INTEGER NOT NULL, rank INTEGER NOT NULL,
          code TEXT NOT NULL, name TEXT NOT NULL, level TEXT NOT NULL,
          is_formal INTEGER NOT NULL, is_supplement INTEGER NOT NULL,
          price REAL NOT NULL, change_pct REAL NOT NULL, sector_code TEXT NOT NULL,
          sector_name TEXT NOT NULL, fund_label TEXT NOT NULL, explanation TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          FOREIGN KEY(snapshot_id) REFERENCES candidate_snapshots(id) ON DELETE CASCADE
        );
        CREATE TABLE web_public_state(state_key TEXT PRIMARY KEY, state_version INTEGER,
          snapshot_id INTEGER, source_ts TEXT, updated_at TEXT, payload_json TEXT);
        ''')
        seed_snapshot(connection)
    if extension:
        with database(path, write=True) as connection:
            install_on_connection(connection)
    return path
