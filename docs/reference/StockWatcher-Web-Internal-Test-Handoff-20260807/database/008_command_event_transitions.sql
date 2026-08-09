-- StockWatcher schema v7 -> v8 reference migration.
-- Run through SQLiteStore only, after a SQLite backup API snapshot.
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;

BEGIN IMMEDIATE;

CREATE TEMP TABLE _require_schema_v7 (
    ok INTEGER NOT NULL CHECK (ok = 1)
);
INSERT INTO _require_schema_v7(ok)
VALUES (
    CASE WHEN COALESCE(
        (SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1), -1
    ) = 7 THEN 1 ELSE 0 END
);
DROP TABLE _require_schema_v7;

DROP INDEX IF EXISTS idx_web_events_source_dedupe;
CREATE UNIQUE INDEX idx_web_events_source_dedupe
    ON web_events(event_type, source_kind, source_id)
    WHERE source_kind IS NOT NULL
      AND source_id IS NOT NULL
      AND event_type <> 'command.updated';

DELETE FROM schema_version;
INSERT INTO schema_version(version, applied_at)
VALUES (8, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

COMMIT;

PRAGMA foreign_key_check;
PRAGMA integrity_check;
