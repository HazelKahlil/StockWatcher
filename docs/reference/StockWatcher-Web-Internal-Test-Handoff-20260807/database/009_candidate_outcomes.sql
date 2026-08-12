-- StockWatcher Web schema v8 -> v9 reference migration.
-- Run through SQLiteStore only, after a SQLite backup API snapshot.
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;

BEGIN IMMEDIATE;

CREATE TEMP TABLE _require_schema_v8 (
    ok INTEGER NOT NULL CHECK (ok = 1)
);
INSERT INTO _require_schema_v8(ok)
VALUES (
    CASE WHEN COALESCE(
        (SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1), -1
    ) = 8 THEN 1 ELSE 0 END
);
DROP TABLE _require_schema_v8;

CREATE TABLE candidate_outcomes (
    id INTEGER PRIMARY KEY,
    entry_snapshot_id INTEGER NOT NULL,
    entry_alert_id INTEGER NOT NULL,
    entry_trade_date TEXT NOT NULL,
    slot TEXT NOT NULL CHECK(slot IN ('09:45', '14:45')),
    rank INTEGER NOT NULL CHECK(rank BETWEEN 1 AND 3),
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    entry_price REAL NOT NULL CHECK(entry_price > 0),
    entry_source_ts TEXT NOT NULL,
    target_trade_date TEXT,
    target_slot TEXT NOT NULL CHECK(target_slot IN ('09:45', '14:45')),
    exit_price REAL,
    exit_source_ts TEXT,
    return_pct REAL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'settled', 'unavailable')),
    outcome TEXT CHECK(outcome IS NULL OR outcome IN ('win', 'loss', 'flat')),
    settlement_method TEXT CHECK(
        settlement_method IS NULL OR settlement_method IN (
            'realtime_scan', 'realtime_batch', 'historical_minute'
        )
    ),
    quality TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    config_version TEXT NOT NULL,
    app_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    safe_reason TEXT,
    settlement_attempts INTEGER NOT NULL DEFAULT 0 CHECK(settlement_attempts >= 0),
    last_attempt_at TEXT,
    next_retry_at TEXT,
    UNIQUE(entry_snapshot_id, slot, rank, code),
    UNIQUE(entry_alert_id, slot, rank, code)
);

CREATE INDEX idx_candidate_outcomes_pending
    ON candidate_outcomes(status, target_trade_date, target_slot, next_retry_at, rank);
CREATE INDEX idx_candidate_outcomes_entry_date
    ON candidate_outcomes(entry_trade_date DESC, slot, rank);
CREATE INDEX idx_candidate_outcomes_code
    ON candidate_outcomes(code, entry_trade_date DESC);

DELETE FROM schema_version;
INSERT INTO schema_version(version, applied_at)
VALUES (9, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

COMMIT;

PRAGMA foreign_key_check;
PRAGMA integrity_check;
