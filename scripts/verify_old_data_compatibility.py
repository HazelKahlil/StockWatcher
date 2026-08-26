#!/usr/bin/env python3
"""Read-only comparison of a pre-migration SQLite copy against a later copy.

Does not open the live database. Prints JSON summaries; never prints secrets.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from stock_watcher.runtime.repeat_tracker import (
    ACTIVATE_AT,
    WINDOW_DAYS,
    calendar_span_days,
    parse_shanghai_timestamp,
    provider_is_countable,
)
from stock_watcher.storage import SQLiteStore

IMMUTABLE_TABLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "notes": ("key", ("key", "value")),
    "daily_summaries": (
        "trade_date",
        (
            "trade_date",
            "generated_at",
            "alert_count",
            "top_sectors_json",
            "repeated_candidates_json",
            "closing_performance_json",
            "fund_summary",
            "health_summary",
            "summary_text",
            "version",
        ),
    ),
    "config_versions": (
        "version",
        ("version", "source", "settings_json", "created_at"),
    ),
    "candidate_snapshots": (
        "id",
        (
            "id",
            "source_ts",
            "generated_at",
            "health",
            "overall_weak",
            "provider_version",
            "config_version",
            "app_version",
            "payload_json",
        ),
    ),
    "candidate_items": (
        "id",
        (
            "id",
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
            "payload_json",
        ),
    ),
    "alert_events": (
        "id",
        (
            "id",
            "snapshot_id",
            "displayed_at",
            "decision",
            "channel",
            "trigger_type",
            "detail_json",
        ),
    ),
}

OUTCOME_IDENTITY = (
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
    "provider_version",
    "config_version",
    "app_version",
    "created_at",
)

RUNTIME_TABLES = (
    "runtime_sessions",
    "web_sessions",
    "service_leases",
    "web_events",
    "web_public_state",
    "automation_tasks",
    "web_users",
    "daily_summaries",
    "candidate_outcomes",
)

NON_COUNTABLE_MARKERS = ("mock", "replay", "synthetic", "demo", "fixture")


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def table_sql(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name"
    ).fetchall()
    return {str(row[0]): str(row[1] or "") for row in rows}


def compare_immutable(old: sqlite3.Connection, new: sqlite3.Connection) -> dict[str, Any]:
    report: dict[str, Any] = {}
    missing_total = 0
    mutated_total = 0
    for table, (pk, columns) in IMMUTABLE_TABLES.items():
        old_cols = {row[1] for row in old.execute(f"PRAGMA table_info({table})")}
        use_cols = [column for column in columns if column in old_cols]
        select = ", ".join(use_cols)
        old_rows = {
            row[pk]: tuple(row[column] for column in use_cols)
            for row in old.execute(f"SELECT {select} FROM {table}")
        }
        new_rows = {
            row[pk]: tuple(row[column] for column in use_cols)
            for row in new.execute(f"SELECT {select} FROM {table}")
        }
        missing = sorted(str(key) for key in old_rows.keys() - new_rows.keys())
        mutated = sorted(
            str(key)
            for key, value in old_rows.items()
            if key in new_rows and new_rows[key] != value
        )
        missing_total += len(missing)
        mutated_total += len(mutated)
        report[table] = {
            "old_count": len(old_rows),
            "new_count": len(new_rows),
            "missing": len(missing),
            "mutated": len(mutated),
            "missing_ids_sample": missing[:10],
            "mutated_ids_sample": mutated[:10],
        }
    report["OLD_ROWS_MISSING"] = missing_total
    report["OLD_ROWS_MUTATED"] = mutated_total
    return report


def compare_outcomes(old: sqlite3.Connection, new: sqlite3.Connection) -> dict[str, Any]:
    old_cols = {row[1] for row in old.execute("PRAGMA table_info(candidate_outcomes)")}
    cols = [column for column in OUTCOME_IDENTITY if column in old_cols]
    select = ", ".join(cols)
    old_rows = {
        row["id"]: tuple(row[column] for column in cols)
        for row in old.execute(f"SELECT {select} FROM candidate_outcomes")
    }
    new_rows = {
        row["id"]: tuple(row[column] for column in cols)
        for row in new.execute(f"SELECT {select} FROM candidate_outcomes")
    }
    missing = sorted(old_rows.keys() - new_rows.keys())
    mutated = sorted(
        key for key, value in old_rows.items() if key in new_rows and new_rows[key] != value
    )
    return {
        "old_count": len(old_rows),
        "new_count": len(new_rows),
        "identity_missing": len(missing),
        "identity_mutated": len(mutated),
        "missing_ids_sample": missing[:10],
        "mutated_ids_sample": mutated[:10],
    }


def compare_app_settings(old: sqlite3.Connection, new: sqlite3.Connection) -> dict[str, Any]:
    old_tables = {
        row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "app_settings" not in old_tables:
        return {"present_in_old": False}
    old_rows = {
        row["key"]: row["value_json"]
        for row in old.execute("SELECT key, value_json FROM app_settings")
    }
    new_rows = {
        row["key"]: row["value_json"]
        for row in new.execute("SELECT key, value_json FROM app_settings")
    }
    allowed_new = {"candidate_repeat_backfill_status"}
    missing = sorted(old_rows.keys() - new_rows.keys())
    mutated = sorted(
        key for key, value in old_rows.items() if key in new_rows and new_rows[key] != value
    )
    extra = sorted(new_rows.keys() - old_rows.keys())
    unexpected_extra = [key for key in extra if key not in allowed_new]
    return {
        "old_count": len(old_rows),
        "new_count": len(new_rows),
        "missing": len(missing),
        "mutated": len(mutated),
        "extra": extra,
        "unexpected_extra": unexpected_extra,
        "missing_keys_sample": missing[:10],
        "mutated_keys_sample": mutated[:10],
    }


def counts(connection: sqlite3.Connection) -> dict[str, int]:
    output: dict[str, int] = {}
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    tracked = (
        *IMMUTABLE_TABLES,
        *RUNTIME_TABLES,
        "candidate_repeat_days",
        "candidate_repeat_states",
    )
    for table in tracked:
        if table in tables:
            output[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return output


def schema_version(connection: sqlite3.Connection) -> int | None:
    row = connection.execute(
        "SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    return int(row[0]) if row else None


def integrity(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(str(path))
    try:
        ok = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
        return {"integrity_check": ok, "foreign_key_check_rows": len(foreign)}
    finally:
        connection.close()


def migrate_copy(src: Path, dest: Path) -> dict[str, Any]:
    dest.write_bytes(src.read_bytes())
    before = sqlite3.connect(str(dest))
    master_before = table_sql(before)
    version_before = schema_version(before)
    before.close()
    store = SQLiteStore(dest)
    store.initialize()
    store.initialize()
    after = sqlite3.connect(str(dest))
    master_after = table_sql(after)
    version_after = schema_version(after)
    added = sorted(set(master_after) - set(master_before))
    removed = sorted(set(master_before) - set(master_after))
    changed = sorted(
        name
        for name in set(master_before) & set(master_after)
        if master_before[name] != master_after[name]
    )
    after.close()
    return {
        "version_before": version_before,
        "version_after": version_after,
        "added_schema_objects": added,
        "removed_schema_objects": removed,
        "changed_schema_objects": changed,
        **integrity(dest),
    }


def provenance(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    snapshots = connection.execute(
        "SELECT id, source_ts, generated_at, health, provider_version, config_version, app_version "
        "FROM candidate_snapshots ORDER BY id"
    ).fetchall()
    skipped: Counter[str] = Counter()
    used: list[sqlite3.Row] = []
    for row in snapshots:
        if str(row["health"]) != "HEALTHY":
            skipped[f"health:{row['health']}"] += 1
            continue
        if not provider_is_countable(row["provider_version"]):
            skipped["provider_not_countable"] += 1
            continue
        source = str(row["source_ts"] or "")
        if not source:
            skipped["missing_source_ts"] += 1
            continue
        parsed = parse_shanghai_timestamp(source)
        if parsed is None:
            skipped["unparseable_or_naive_source_ts"] += 1
            continue
        items = connection.execute(
            "SELECT COUNT(*) FROM candidate_items WHERE snapshot_id = ?",
            (row["id"],),
        ).fetchone()[0]
        if int(items) != 3:
            skipped["item_count_not_3"] += 1
            continue
        used.append(row)
    providers = Counter(str(row["provider_version"]) for row in used)
    configs = Counter(str(row["config_version"]) for row in used)
    apps = Counter(str(row["app_version"]) for row in used)
    dates = sorted({str(row["source_ts"])[:10] for row in used})
    tz_offsets: Counter[str] = Counter()
    for row in used:
        source = str(row["source_ts"])
        if source.endswith("Z"):
            tz_offsets["Z"] += 1
        elif "+" in source:
            tz_offsets[source[source.rfind("+") :]] += 1
        elif source.count("-") >= 3:
            tz_offsets[source[source.rfind("-") :]] += 1
        else:
            tz_offsets["unspecified"] += 1
    trigger_types = {
        str(row[0]): int(row[1])
        for row in connection.execute(
            "SELECT COALESCE(trigger_type, ''), COUNT(*) FROM alert_events GROUP BY 1"
        )
    }
    days = connection.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT trade_date) AS dates, COUNT(DISTINCT code) AS codes, "
        "MIN(trade_date), MAX(trade_date) FROM candidate_repeat_days"
    ).fetchone()
    active = connection.execute(
        "SELECT COUNT(*) FROM candidate_repeat_states WHERE active = 1"
    ).fetchone()[0]
    dupes = connection.execute(
        "SELECT COUNT(*) FROM (SELECT code, trade_date FROM candidate_repeat_days "
        "GROUP BY code, trade_date HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    connection.close()
    return {
        "filters": {
            "health": "HEALTHY",
            "provider_is_countable": True,
            "non_countable_markers": list(NON_COUNTABLE_MARKERS),
            "source_ts": "parseable timezone-aware Asia/Shanghai (or +08:00 offset)",
            "displayed_items": 3,
        },
        "snapshots_total": len(snapshots),
        "snapshots_used": len(used),
        "skipped": dict(skipped),
        "provider_version": dict(providers),
        "config_version": dict(configs),
        "app_version": dict(apps),
        "source_min": used[0]["source_ts"] if used else None,
        "source_max": used[-1]["source_ts"] if used else None,
        "source_tz_offsets": dict(tz_offsets),
        "alert_trigger_types": trigger_types,
        "distinct_trade_dates_from_snapshots": len(dates),
        "trade_date_min": dates[0] if dates else None,
        "trade_date_max": dates[-1] if dates else None,
        "repeat_days_rows": days[0],
        "repeat_days_distinct_trade_date": days[1],
        "repeat_days_distinct_code": days[2],
        "repeat_days_min": days[3],
        "repeat_days_max": days[4],
        "active_states": active,
        "duplicate_code_trade_date_groups": dupes,
        "non_production_used": sum(
            count
            for key, count in providers.items()
            if any(marker in key.casefold() for marker in NON_COUNTABLE_MARKERS)
        ),
    }


def activation_sample(path: Path, limit: int = 90) -> dict[str, Any]:
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    states = connection.execute(
        "SELECT code, active, sequence_started_on, occurrence_count, span_days, "
        "activated_trade_date, last_seen_on FROM candidate_repeat_states WHERE active = 1 "
        "ORDER BY code LIMIT ?",
        (limit,),
    ).fetchall()
    failures: list[str] = []
    checked = 0
    for state in states:
        checked += 1
        dates = [
            date.fromisoformat(row[0])
            for row in connection.execute(
                "SELECT trade_date FROM candidate_repeat_days WHERE code = ? AND trade_date >= ? "
                "ORDER BY trade_date",
                (state["code"], state["sequence_started_on"]),
            ).fetchall()
        ]
        if len(dates) < ACTIVATE_AT:
            failures.append(f"{state['code']}: fewer than 3 dates after sequence start")
            continue
        window = dates[:ACTIVATE_AT]
        if calendar_span_days(window[0], window[2]) > WINDOW_DAYS:
            span = calendar_span_days(window[0], window[2])
            failures.append(f"{state['code']}: first three dates span {span}")
        if int(state["occurrence_count"]) != len(dates):
            failures.append(
                f"{state['code']}: occurrence_count {state['occurrence_count']} != {len(dates)}"
            )
        if int(state["span_days"]) != calendar_span_days(dates[0], dates[-1]):
            failures.append(f"{state['code']}: span mismatch")
        if int(state["active"]) != 1:
            failures.append(f"{state['code']}: active flipped")
        history = connection.execute(
            "SELECT trade_date, active_after, count_after FROM candidate_repeat_days "
            "WHERE code = ? ORDER BY trade_date",
            (state["code"],),
        ).fetchall()
        purple_started = False
        for row in history:
            if int(row["count_after"]) < ACTIVATE_AT and int(row["active_after"]) == 1:
                failures.append(f"{state['code']} {row['trade_date']}: purple before third day")
            if int(row["count_after"]) >= ACTIVATE_AT and int(row["active_after"]) != 1:
                failures.append(f"{state['code']} {row['trade_date']}: missing purple after third")
            if int(row["active_after"]) == 1:
                purple_started = True
            elif purple_started:
                code_date = f"{state['code']} {row['trade_date']}"
                failures.append(f"{code_date}: history un-purpled after activation")
    connection.close()
    return {"checked_active": checked, "failures": failures[:20], "failure_count": len(failures)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        required=True,
        choices=("compare", "migrate", "provenance", "activation"),
    )
    parser.add_argument("--old", type=Path)
    parser.add_argument("--new", type=Path)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "compare":
        assert args.old and args.new
        old = connect(args.old)
        new = connect(args.new)
        payload = {
            "old_schema_version": schema_version(old),
            "new_schema_version": schema_version(new),
            "old_counts": counts(old),
            "new_counts": counts(new),
            "immutable": compare_immutable(old, new),
            "outcomes_identity": compare_outcomes(old, new),
            "app_settings_old_keys": compare_app_settings(old, new),
            "old_integrity": integrity(args.old),
            "new_integrity": integrity(args.new),
        }
        old.close()
        new.close()
        payload["OLD_ROWS_MISSING"] = payload["immutable"]["OLD_ROWS_MISSING"]
        payload["OLD_ROWS_MUTATED"] = payload["immutable"]["OLD_ROWS_MUTATED"]
    elif args.mode == "migrate":
        assert args.old and args.new
        payload = migrate_copy(args.old, args.new)
    elif args.mode == "provenance":
        assert args.db
        payload = provenance(args.db)
    else:
        assert args.db
        payload = activation_sample(args.db)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
