from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

from stock_watcher.storage import SQLiteStore

AUDIT_FIELDS = (
    "scan_id",
    "completed_at",
    "trigger_type",
    "warmup_state",
    "raw_rank",
    "code",
    "name",
    "sector",
    "sector_type",
    "total_score",
    "level",
    "is_formal",
    "velocity_available",
    "selected_raw",
    "selected_stable",
    "decision",
)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def _audits(rows: list[dict[str, object]]) -> list[tuple[dict[str, object], dict[str, object]]]:
    """Yield (scan, audit) pairs with parsed audit_json."""
    output: list[tuple[dict[str, object], dict[str, object]]] = []
    for scan in rows:
        try:
            audit = json.loads(str(scan.get("audit_json") or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(audit, dict):
            continue
        output.append((scan, audit))
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export raw-vs-stable Top3 evidence from StockWatcher SQLite."
    )
    parser.add_argument("database", type=Path)
    parser.add_argument("trade_date", help="YYYY-MM-DD")
    parser.add_argument("output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    store = SQLiteStore(args.database, read_only=True)
    rows = store.list_scan_runs(args.trade_date)
    args.output.mkdir(parents=True, exist_ok=True)

    # 1. scan-runs.json / scan-runs.csv
    (args.output / "scan-runs.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    scan_fields = [
        "id",
        "started_at",
        "completed_at",
        "trigger_type",
        "task_key",
        "health",
        "coverage_ratio",
        "elapsed_seconds",
        "source_age_seconds",
        "detail",
    ]
    _write_csv(
        args.output / "scan-runs.csv",
        scan_fields,
        [{key: row.get(key) for key in scan_fields} for row in rows],
    )

    pairs = _audits(rows)

    # 2. candidate audit rows (raw Top20 evidence)
    audit_rows: list[dict[str, object]] = []
    for scan, audit in pairs:
        for candidate in audit.get("rows", []):
            if not isinstance(candidate, dict):
                continue
            audit_rows.append(
                {
                    "scan_id": scan["id"],
                    "completed_at": scan["completed_at"],
                    "trigger_type": scan["trigger_type"],
                    "warmup_state": audit.get("warmup_state", "unknown"),
                    **{
                        key: candidate.get(key)
                        for key in AUDIT_FIELDS
                        if key
                        not in {
                            "scan_id",
                            "completed_at",
                            "trigger_type",
                            "warmup_state",
                        }
                    },
                }
            )
    _write_csv(args.output / "candidate-audit.csv", list(AUDIT_FIELDS), audit_rows)

    # 3. raw-top20.csv
    raw_fields = ["scan_id", "completed_at", "rank", "code", "name", "sector", "level"]
    raw_rows: list[dict[str, object]] = []
    for scan, audit in pairs:
        raw_codes = audit.get("raw_codes", [])
        for index, code in enumerate(raw_codes[:20], start=1):
            raw_rows.append(
                {"scan_id": scan["id"], "completed_at": scan["completed_at"], "rank": index, "code": code}
            )
    _write_csv(args.output / "raw-top20.csv", raw_fields, raw_rows)

    # 4. stable-top3-timeline.csv
    stable_fields = ["scan_id", "completed_at", "rank", "code", "name", "decision"]
    stable_rows: list[dict[str, object]] = []
    for scan, audit in pairs:
        stable_codes = audit.get("stable_codes", [])
        for index, code in enumerate(stable_codes, start=1):
            stable_rows.append(
                {"scan_id": scan["id"], "completed_at": scan["completed_at"], "rank": index, "code": code}
            )
    _write_csv(args.output / "stable-top3-timeline.csv", stable_fields, stable_rows)

    # 5. excluded-candidates.csv
    excluded_fields = [
        "scan_id",
        "completed_at",
        "code",
        "name",
        "raw_rank",
        "total_score",
        "level",
        "decision",
        "velocity_available",
    ]
    excluded_rows: list[dict[str, object]] = []
    for scan, audit in pairs:
        for candidate in audit.get("rows", []):
            if not isinstance(candidate, dict):
                continue
            if candidate.get("selected_stable"):
                continue
            excluded_rows.append(
                {
                    "scan_id": scan["id"],
                    "completed_at": scan["completed_at"],
                    **{key: candidate.get(key) for key in excluded_fields if key not in {"scan_id", "completed_at"}},
                }
            )
    _write_csv(args.output / "excluded-candidates.csv", excluded_fields, excluded_rows)

    # 6. automation-tasks.csv
    automation_fields = [
        "task_key",
        "task_type",
        "trade_date",
        "target_at",
        "deadline_at",
        "state",
        "updated_at",
        "attempts",
        "detail",
    ]
    automation_rows = [
        {key: task.get(key) for key in automation_fields}
        for task in store.list_automation_tasks(args.trade_date)
    ]
    _write_csv(args.output / "automation-tasks.csv", automation_fields, automation_rows)

    # 7. runtime-sessions.csv
    session_fields = [
        "session_id",
        "pid",
        "app_path",
        "source_commit",
        "started_at",
        "last_heartbeat_at",
        "last_scan_at",
        "last_sleep_at",
        "last_wake_at",
        "ended_at",
        "exit_reason",
        "graceful_exit",
        "previous_unclean_exit",
    ]
    session_rows = [
        {key: session.get(key) for key in session_fields}
        for session in store.list_runtime_sessions(args.trade_date)
    ]
    _write_csv(args.output / "runtime-sessions.csv", session_fields, session_rows)

    # 8. scheduler-events.csv
    event_fields = ["session_id", "occurred_at", "event_type", "detail_json"]
    event_rows: list[dict[str, object]] = []
    for session in store.list_runtime_sessions(args.trade_date):
        for event in store.list_runtime_events(str(session["session_id"])):
            if not str(event.get("occurred_at", "")).startswith(args.trade_date):
                continue
            event_rows.append({key: event.get(key) for key in event_fields})
    event_rows.sort(key=lambda row: str(row.get("occurred_at", "")))
    _write_csv(args.output / "scheduler-events.csv", event_fields, event_rows)

    # 9. cache-status.csv
    cache_path = args.database.parent / "runtime-universe-v1.json"
    cache_rows: list[dict[str, object]] = [{"aspect": "universe_cache_file", "status": "missing"}]
    if cache_path.is_file():
        cache_rows = [
            {
                "aspect": "universe_cache_file",
                "status": "exists",
                "size_bytes": cache_path.stat().st_size,
                "modified_at": datetime.fromtimestamp(cache_path.stat().st_mtime).isoformat(),
            }
        ]
        try:
            document = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(document, dict):
                cache_rows.append({"aspect": "concept_loaded", "status": bool(document.get("concept_loaded"))})
                generated = document.get("generated_at") or document.get("prepared_at")
                if generated:
                    cache_rows.append({"aspect": "cache_generated_at", "status": generated})
        except (OSError, json.JSONDecodeError):
            cache_rows.append({"aspect": "concept_loaded", "status": "unreadable"})
    _write_csv(
        args.output / "cache-status.csv",
        ["aspect", "status", "size_bytes", "modified_at"],
        cache_rows,
    )

    # 10. alert-events.csv
    alert_fields = [
        "alert_id",
        "displayed_at",
        "decision",
        "trigger_type",
        "detail_json",
        "snapshot_id",
        "overall_weak",
    ]
    alert_rows: list[dict[str, object]] = []
    for alert in store.list_alert_history(
        now=datetime.fromisoformat(f"{args.trade_date}T15:31:00+08:00"),
        days=1,
    ):
        if not str(alert.get("displayed_at", "")).startswith(args.trade_date):
            continue
        alert_rows.append({key: alert.get(key) for key in alert_fields})
    _write_csv(args.output / "alert-events.csv", alert_fields, alert_rows)

    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
