PRAGMA journal_mode=WAL;

CREATE TABLE instrument (
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  exchange TEXT NOT NULL,
  board TEXT NOT NULL,
  list_date TEXT,
  is_st INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE sector (
  sector_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  sector_type TEXT NOT NULL,
  source TEXT NOT NULL,
  version TEXT NOT NULL
);

CREATE TABLE sector_member (
  sector_id TEXT NOT NULL,
  code TEXT NOT NULL,
  valid_from TEXT NOT NULL,
  valid_to TEXT,
  PRIMARY KEY (sector_id, code, valid_from)
);

CREATE TABLE ranking_snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_ts TEXT NOT NULL,
  receive_ts TEXT NOT NULL,
  overall_label TEXT NOT NULL,
  market_regime TEXT,
  data_health TEXT NOT NULL,
  config_version TEXT NOT NULL,
  top3_json TEXT NOT NULL
);

CREATE TABLE alert_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_type TEXT NOT NULL,
  source_ts TEXT NOT NULL,
  shown_ts TEXT NOT NULL,
  dedupe_key TEXT NOT NULL UNIQUE,
  top3_json TEXT NOT NULL,
  trigger_reason TEXT NOT NULL,
  push_status TEXT,
  config_version TEXT NOT NULL
);

CREATE TABLE feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id INTEGER NOT NULL,
  code TEXT,
  rating TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE system_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_ts TEXT NOT NULL,
  level TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL
);

CREATE TABLE config_version (
  version TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  config_json TEXT NOT NULL,
  source TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 0
);
