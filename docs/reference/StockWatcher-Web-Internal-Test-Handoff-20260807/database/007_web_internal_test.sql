-- StockWatcher schema v6 -> v7 reference migration.
-- The production implementation must run this through the Python migration
-- runner after creating a SQLite backup with the backup API.
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;

BEGIN IMMEDIATE;

CREATE TEMP TABLE _require_schema_v6 (
    ok INTEGER NOT NULL CHECK (ok = 1)
);
INSERT INTO _require_schema_v6(ok)
VALUES (
    CASE WHEN COALESCE(
        (SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1), -1
    ) = 6 THEN 1 ELSE 0 END
);
DROP TABLE _require_schema_v6;

CREATE TABLE IF NOT EXISTS web_users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('tester', 'admin')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT,
    password_changed_at TEXT NOT NULL,
    created_by INTEGER,
    FOREIGN KEY(created_by) REFERENCES web_users(user_id)
);

CREATE TABLE IF NOT EXISTS web_sessions (
    session_token_hash TEXT PRIMARY KEY CHECK (length(session_token_hash) = 64),
    user_id INTEGER NOT NULL,
    csrf_token_hash TEXT NOT NULL CHECK (length(csrf_token_hash) = 64),
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    idle_expires_at TEXT NOT NULL,
    absolute_expires_at TEXT NOT NULL,
    revoked_at TEXT,
    ip_hash TEXT,
    user_agent TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(user_id) REFERENCES web_users(user_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_web_sessions_user
    ON web_sessions(user_id, revoked_at, absolute_expires_at);
CREATE INDEX IF NOT EXISTS idx_web_sessions_expiry
    ON web_sessions(absolute_expires_at, idle_expires_at);

CREATE TABLE IF NOT EXISTS web_user_state (
    user_id INTEGER PRIMARY KEY,
    last_event_id INTEGER NOT NULL DEFAULT 0,
    browser_notifications_enabled INTEGER NOT NULL DEFAULT 0
        CHECK (browser_notifications_enabled IN (0, 1)),
    sound_enabled INTEGER NOT NULL DEFAULT 0 CHECK (sound_enabled IN (0, 1)),
    updated_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES web_users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS service_leases (
    lease_name TEXT PRIMARY KEY,
    holder_id TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    fencing_token INTEGER NOT NULL DEFAULT 1 CHECK (fencing_token > 0)
);
CREATE INDEX IF NOT EXISTS idx_service_leases_expiry ON service_leases(expires_at);

CREATE TABLE IF NOT EXISTS secret_requests (
    request_id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL CHECK (purpose IN ('token_test', 'token_update')),
    ciphertext_b64 TEXT NOT NULL,
    nonce_b64 TEXT NOT NULL,
    key_version INTEGER NOT NULL CHECK (key_version > 0),
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 8),
    requested_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'consumed', 'expired', 'failed')),
    FOREIGN KEY(requested_by) REFERENCES web_users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_secret_requests_status_expiry
    ON secret_requests(status, expires_at);

CREATE TABLE IF NOT EXISTS encrypted_secrets (
    secret_name TEXT NOT NULL,
    slot TEXT NOT NULL CHECK (slot IN ('active', 'previous')),
    ciphertext_b64 TEXT NOT NULL,
    nonce_b64 TEXT NOT NULL,
    key_version INTEGER NOT NULL CHECK (key_version > 0),
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 8),
    status TEXT NOT NULL CHECK (status IN ('active', 'previous', 'revoked')),
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    last_tested_at TEXT,
    capability_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(secret_name, slot),
    FOREIGN KEY(updated_by) REFERENCES web_users(user_id)
);

CREATE TABLE IF NOT EXISTS web_commands (
    command_id TEXT PRIMARY KEY,
    command_type TEXT NOT NULL CHECK (command_type IN (
        'manual_refresh', 'universe_refresh', 'token_test',
        'token_update', 'summary_generate'
    )),
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'running', 'succeeded', 'failed', 'cancelled', 'expired'
    )),
    requested_by INTEGER NOT NULL,
    requested_at TEXT NOT NULL,
    idempotency_key TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    secret_request_id TEXT,
    claimed_by TEXT,
    fencing_token INTEGER,
    started_at TEXT,
    completed_at TEXT,
    expires_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    result_json TEXT,
    error_code TEXT,
    error_detail TEXT,
    FOREIGN KEY(requested_by) REFERENCES web_users(user_id),
    FOREIGN KEY(secret_request_id) REFERENCES secret_requests(request_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_web_commands_idempotency
    ON web_commands(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_web_commands_active_manual_refresh
    ON web_commands(command_type)
    WHERE command_type = 'manual_refresh' AND status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS idx_web_commands_claim
    ON web_commands(status, requested_at, command_type);
CREATE INDEX IF NOT EXISTS idx_web_commands_requester
    ON web_commands(requested_by, requested_at);

CREATE TABLE IF NOT EXISTS web_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    correlation_id TEXT,
    source_kind TEXT,
    source_id TEXT,
    visibility TEXT NOT NULL DEFAULT 'all'
        CHECK (visibility IN ('all', 'tester', 'admin')),
    payload_json TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_web_events_type_time
    ON web_events(event_type, occurred_at);
CREATE INDEX IF NOT EXISTS idx_web_events_expiry ON web_events(expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_web_events_source_dedupe
    ON web_events(event_type, source_kind, source_id)
    WHERE source_kind IS NOT NULL AND source_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS web_public_state (
    state_key TEXT PRIMARY KEY CHECK (state_key = 'current'),
    state_version INTEGER NOT NULL CHECK (state_version >= 0),
    snapshot_id INTEGER,
    source_ts TEXT,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(snapshot_id) REFERENCES candidate_snapshots(id)
);

CREATE TABLE IF NOT EXISTS web_audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    actor_user_id INTEGER,
    actor_session_hash_prefix TEXT,
    action TEXT NOT NULL,
    object_type TEXT,
    object_id TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed', 'denied')),
    request_id TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(actor_user_id) REFERENCES web_users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_web_audit_time ON web_audit_log(occurred_at);
CREATE INDEX IF NOT EXISTS idx_web_audit_actor ON web_audit_log(actor_user_id, occurred_at);

DELETE FROM schema_version;
INSERT INTO schema_version(version, applied_at)
VALUES (7, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));

COMMIT;

PRAGMA foreign_key_check;
PRAGMA integrity_check;
