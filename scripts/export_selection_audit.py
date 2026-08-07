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
    "sector_code",
    "sector_type",
    "total_score",
    "core_score",
    "level",
    "is_formal",
    "is_supplement",
    "velocity_available",
    "velocity_1m_pct",
    "velocity_3m_pct",
    "velocity_5m_pct",
    "selected_raw",
    "selected_stable",
    "decision",
)

RANKING_FIELDS = (
    "scan_id",
    "completed_at",
    "trigger_type",
    "feature_readiness",
    "rank",
    "raw_rank",
    "code",
    "name",
    "sector",
    "sector_code",
    "sector_type",
    "total_score",
    "core_score",
    "level",
    "is_formal",
    "is_supplement",
    "velocity_available",
    "velocity_1m_pct",
    "velocity_3m_pct",
    "velocity_5m_pct",
    "selected_raw",
    "selected_stable",
    "decision",
    "selection_reason",
)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def _write_json(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _parse_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _audit_row_index(audit: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = audit.get("rows")
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("code")): row
        for row in rows
        if isinstance(row, dict) and row.get("code")
    }


def _batch_candidate_index(value: object) -> dict[str, dict[str, object]]:
    payload = _parse_mapping(value)
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return {}
    return {
        str(candidate.get("code")): candidate
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("code")
    }


def _rank_value(row: dict[str, object]) -> int:
    value = row.get("raw_rank")
    if isinstance(value, bool):
        return 10**9
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return 10**9


def _candidate_export_row(
    *,
    scan: dict[str, object],
    audit: dict[str, object],
    candidate: dict[str, object],
    rank: int,
    displayed_candidate: dict[str, object] | None = None,
) -> dict[str, object]:
    effective = dict(candidate)
    if displayed_candidate is not None:
        # The batch payload contains the actual displayed form after sector
        # diversification and supplement conversion.  Preserve audit-only
        # ranking/decision fields when overlaying it.
        effective.update(displayed_candidate)
        for key in ("raw_rank", "decision", "selected_raw", "selected_stable"):
            if key in candidate:
                effective[key] = candidate[key]
    is_formal = bool(effective.get("is_formal"))
    reasons = effective.get("reasons")
    reason_text = (
        "｜".join(str(item) for item in reasons if str(item).strip())
        if isinstance(reasons, list)
        else ""
    )
    decision = str(effective.get("decision") or "")
    return {
        "scan_id": scan.get("id"),
        "completed_at": scan.get("completed_at"),
        "trigger_type": scan.get("trigger_type"),
        "feature_readiness": audit.get("warmup_state", "unknown"),
        "rank": rank,
        "raw_rank": effective.get("raw_rank"),
        "code": effective.get("code"),
        "name": effective.get("name"),
        "sector": effective.get("sector"),
        "sector_code": effective.get("sector_code"),
        "sector_type": effective.get("sector_type"),
        "total_score": effective.get("total_score"),
        "core_score": effective.get("core_score"),
        "level": effective.get("level"),
        "is_formal": is_formal,
        "is_supplement": effective.get("is_supplement", not is_formal),
        "velocity_available": effective.get("velocity_available"),
        "velocity_1m_pct": effective.get("velocity_1m_pct"),
        "velocity_3m_pct": effective.get("velocity_3m_pct"),
        "velocity_5m_pct": effective.get("velocity_5m_pct"),
        "selected_raw": effective.get("selected_raw"),
        "selected_stable": effective.get("selected_stable"),
        "decision": decision,
        "selection_reason": "｜".join(
            part for part in (decision, reason_text) if part
        ),
    }


def _audits(rows: list[dict[str, object]]) -> list[tuple[dict[str, object], dict[str, object]]]:
    """Yield ``(scan, audit)`` pairs with parsed audit JSON."""
    output: list[tuple[dict[str, object], dict[str, object]]] = []
    for scan in rows:
        audit = _parse_mapping(scan.get("audit_json"))
        if audit:
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

    # 1. Scan runs.
    _write_json(args.output / "scan-runs.json", rows)
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

    # 2. Complete candidate audit rows.
    audit_rows: list[dict[str, object]] = []
    for scan, audit in pairs:
        candidates = audit.get("rows")
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            audit_rows.append(
                {
                    "scan_id": scan.get("id"),
                    "completed_at": scan.get("completed_at"),
                    "trigger_type": scan.get("trigger_type"),
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
    _write_json(args.output / "candidate-audit.json", audit_rows)

    # 3. True score-order Top20.  ``raw_codes`` is only the diversified raw
    # business Top3, whereas the audit rows preserve the full score order.
    raw_rows: list[dict[str, object]] = []
    for scan, audit in pairs:
        candidates = sorted(_audit_row_index(audit).values(), key=_rank_value)
        raw_payload = _batch_candidate_index(scan.get("raw_batch_json"))
        for index, candidate in enumerate(candidates[:20], start=1):
            code = str(candidate.get("code", ""))
            raw_rows.append(
                _candidate_export_row(
                    scan=scan,
                    audit=audit,
                    candidate=candidate,
                    rank=index,
                    displayed_candidate=raw_payload.get(code),
                )
            )
    _write_csv(args.output / "raw-top20.csv", list(RANKING_FIELDS), raw_rows)
    _write_json(args.output / "raw-top20.json", raw_rows)

    # 4. Stable displayed Top3 timeline.
    stable_rows: list[dict[str, object]] = []
    for scan, audit in pairs:
        indexed = _audit_row_index(audit)
        stable_payload = _batch_candidate_index(scan.get("stable_batch_json"))
        stable_codes = audit.get("stable_codes")
        if not isinstance(stable_codes, list):
            continue
        for index, code_value in enumerate(stable_codes, start=1):
            code = str(code_value)
            candidate = indexed.get(code)
            if candidate is None:
                candidate = {
                    "code": code,
                    "decision": "stable_metadata_missing",
                    "selected_stable": True,
                }
            stable_rows.append(
                _candidate_export_row(
                    scan=scan,
                    audit=audit,
                    candidate=candidate,
                    rank=index,
                    displayed_candidate=stable_payload.get(code),
                )
            )
    _write_csv(
        args.output / "stable-top3-timeline.csv",
        list(RANKING_FIELDS),
        stable_rows,
    )
    _write_json(args.output / "stable-top3-timeline.json", stable_rows)

    # 5. Excluded/non-displayed candidates.
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
        candidates = audit.get("rows")
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("selected_stable"):
                continue
            excluded_rows.append(
                {
                    "scan_id": scan.get("id"),
                    "completed_at": scan.get("completed_at"),
                    **{
                        key: candidate.get(key)
                        for key in excluded_fields
                        if key not in {"scan_id", "completed_at"}
                    },
                }
            )
    _write_csv(args.output / "excluded-candidates.csv", excluded_fields, excluded_rows)
    _write_json(args.output / "excluded-candidates.json", excluded_rows)

    # 6. Automation tasks.
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
    _write_json(args.output / "automation-tasks.json", automation_rows)

    # 7. Runtime sessions.
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
    _write_json(args.output / "runtime-sessions.json", session_rows)

    # 8. Scheduler/runtime events.
    event_fields = ["session_id", "occurred_at", "event_type", "detail_json"]
    event_rows: list[dict[str, object]] = []
    for session in store.list_runtime_sessions(args.trade_date):
        for event in store.list_runtime_events(str(session["session_id"])):
            if not str(event.get("occurred_at", "")).startswith(args.trade_date):
                continue
            event_rows.append({key: event.get(key) for key in event_fields})
    event_rows.sort(key=lambda row: str(row.get("occurred_at", "")))
    _write_csv(args.output / "scheduler-events.csv", event_fields, event_rows)
    _write_json(args.output / "scheduler-events.json", event_rows)

    # 9. Static cache status.  The cache contract nests concept state under
    # ``universe``; reading the top-level key incorrectly exported False.
    cache_path = args.database.parent / "runtime-universe-v1.json"
    cache_rows: list[dict[str, object]] = [
        {"aspect": "universe_cache_file", "status": "missing"}
    ]
    if cache_path.is_file():
        cache_rows = [
            {
                "aspect": "universe_cache_file",
                "status": "exists",
                "size_bytes": cache_path.stat().st_size,
                "modified_at": datetime.fromtimestamp(
                    cache_path.stat().st_mtime
                ).isoformat(),
            }
        ]
        try:
            document = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(document, dict):
                universe = document.get("universe")
                universe_record = universe if isinstance(universe, dict) else {}
                memberships = universe_record.get("memberships")
                membership_rows = memberships if isinstance(memberships, list) else []
                industry_count = sum(
                    isinstance(item, dict) and item.get("sector_type") == "industry"
                    for item in membership_rows
                )
                concept_count = sum(
                    isinstance(item, dict) and item.get("sector_type") == "concept"
                    for item in membership_rows
                )
                cache_rows.extend(
                    [
                        {
                            "aspect": "concept_loaded",
                            "status": bool(universe_record.get("concept_loaded")),
                        },
                        {
                            "aspect": "cache_generated_at",
                            "status": document.get("generated_at", ""),
                        },
                        {
                            "aspect": "cache_schema_version",
                            "status": document.get("schema_version", ""),
                        },
                        {
                            "aspect": "trend_through_date",
                            "status": document.get("trend_through_date", ""),
                        },
                        {
                            "aspect": "membership_count_industry",
                            "status": industry_count,
                        },
                        {
                            "aspect": "membership_count_concept",
                            "status": concept_count,
                        },
                    ]
                )
        except (OSError, json.JSONDecodeError):
            cache_rows.append({"aspect": "concept_loaded", "status": "unreadable"})
    cache_fields = ["aspect", "status", "size_bytes", "modified_at"]
    _write_csv(args.output / "cache-status.csv", cache_fields, cache_rows)
    _write_json(args.output / "cache-status.json", cache_rows)

    # 10. Alert events.
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
    _write_json(args.output / "alert-events.json", alert_rows)

    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
