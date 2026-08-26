#!/usr/bin/env python3
"""Dump ranking/alert/public-state fixtures and compare BASE vs FEATURE.

Repeat fields are stripped from FEATURE payloads before comparison.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from stock_watcher.domain import SHANGHAI, CandidateInput, HealthState, Security
from stock_watcher.engine import (
    AlertPolicy,
    AlertPolicyConfig,
    AlertTrigger,
    CandidateConfig,
    CandidateEngine,
    DailySummaryEngine,
    StableTop3Config,
    StableTop3Selector,
    StrongMovementDetector,
)
from stock_watcher.engine.candidates import Candidate, CandidateBatch
from stock_watcher.services.public_state import PublicStateBuilder
from stock_watcher.services.stockwatcher_service import StockWatcherService
from stock_watcher.storage import SQLiteStore

REPEAT_KEYS = {
    "repeat_active",
    "repeat_count",
    "repeat_span_days",
    "repeat_label",
    "repeat_sequence_started_on",
    "repeat_activated_at",
    "repeat_last_seen_on",
}


def _stamp(hour: int = 10, minute: int = 0) -> datetime:
    return datetime(2026, 7, 23, hour, minute, tzinfo=SHANGHAI)


def _item(code: str, **changes: object) -> CandidateInput:
    values: dict[str, object] = {
        "security": Security(code, f"样本{code}", "SH"),
        "price": 10.0,
        "change_pct": 6.0,
        "velocity_pct": 2.5,
        "sector": "模拟板块",
        "sector_strength": 3.0,
        "trend_3d_pct": 1.0,
        "source_ts": _stamp(),
        "received_ts": _stamp(),
        "provider_version": "tushare-15000",
        "config_version": "v0.2",
    }
    values.update(changes)
    return CandidateInput(**values)  # type: ignore[arg-type]


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        try:
            return value.value
        except Exception:
            return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _strip_repeat(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_repeat(item)
            for key, item in value.items()
            if key not in REPEAT_KEYS
        }
    if isinstance(value, list):
        return [_strip_repeat(item) for item in value]
    return value


def _dump_candidate(candidate: Candidate) -> dict[str, Any]:
    payload = asdict(candidate)
    payload["source_ts"] = candidate.source_ts.isoformat()
    return _jsonable(payload)


def _dump_batch(batch: CandidateBatch | None) -> dict[str, Any] | None:
    if batch is None:
        return None
    return {
        "source_ts": batch.source_ts.isoformat(),
        "health": batch.health.value,
        "overall_weak": batch.overall_weak,
        "fund_module": batch.fund_module,
        "formal_count": batch.formal_count,
        "codes": [candidate.code for candidate in batch.candidates],
        "levels": [candidate.level for candidate in batch.candidates],
        "formal": [candidate.is_formal for candidate in batch.candidates],
        "supplement": [candidate.is_supplement for candidate in batch.candidates],
        "scores": [candidate.total_score for candidate in batch.candidates],
        "candidates": [_dump_candidate(candidate) for candidate in batch.candidates],
        "trace_payload": batch.trace_payload(),
    }


def dump_core() -> dict[str, Any]:
    engine = CandidateEngine()
    config = CandidateConfig("v0.2", "0.2.0")
    inputs = (
        _item("600003"),
        _item("600002"),
        _item("600001", change_pct=1.0, velocity_pct=0.2, sector_strength=0.5),
    )
    healthy = engine.calculate(inputs, HealthState.HEALTHY, config)
    stopped = engine.calculate(inputs, HealthState.STOPPED, config)
    warming = engine.calculate(inputs, HealthState.WARMING, config)
    assert healthy is not None
    replacement_inputs = (
        _item("600001"),
        _item("600002"),
        _item("600004"),
    )
    replacement = engine.calculate(replacement_inputs, HealthState.HEALTHY, config)
    assert replacement is not None

    selector = StableTop3Selector(
        StableTop3Config(minimum_seat_hold_seconds=0, confirmation_cycles=1)
    )
    first_stable = selector.update(healthy, now=_stamp())
    second_stable = selector.update(replacement, now=_stamp(10, 1))

    policy = AlertPolicy(
        AlertPolicyConfig(replacement_cycles=2, replacement_margin=1.0, daily_limit=3)
    )
    now = _stamp(10, 5)
    scheduled_0945 = policy.decide(healthy, now, AlertTrigger.SCHEDULED_0945)
    scheduled_1445 = policy.decide(healthy, _stamp(14, 45), AlertTrigger.SCHEDULED_1445)
    first_intraday = policy.decide(
        healthy, now + timedelta(minutes=1), AlertTrigger.INTRADAY, triggering_codes=("600002",)
    )
    cooldown = policy.decide(
        replacement,
        now + timedelta(minutes=2),
        AlertTrigger.INTRADAY,
        triggering_codes=("600004",),
    )
    after_cooldown = policy.decide(
        replacement,
        now + timedelta(minutes=7),
        AlertTrigger.INTRADAY,
        triggering_codes=("600004",),
    )
    daily_hits = []
    late_policy = AlertPolicy(AlertPolicyConfig(daily_limit=1, cooldown=timedelta(seconds=0)))
    daily_hits.append(
        late_policy.decide(
            healthy,
            _stamp(10, 20),
            AlertTrigger.INTRADAY,
            triggering_codes=("600002",),
        )
    )
    daily_hits.append(
        late_policy.decide(
            replacement, _stamp(10, 21), AlertTrigger.INTRADAY, triggering_codes=("600004",)
        )
    )

    detector = StrongMovementDetector()
    quiet = engine.calculate(
        (
            _item("600001", velocity_pct=0.1, sector_strength=0.2),
            _item("600002", velocity_pct=0.1, sector_strength=0.2),
            _item("600003", velocity_pct=0.1, sector_strength=0.2),
        ),
        HealthState.HEALTHY,
        config,
    )
    fast = engine.calculate(
        (
            _item("600001", velocity_pct=3.0, sector_strength=4.0),
            _item("600002", velocity_pct=3.0, sector_strength=4.0),
            _item("600003", velocity_pct=0.2, sector_strength=0.4),
        ),
        HealthState.HEALTHY,
        config,
    )
    assert quiet is not None and fast is not None
    first_move = detector.evaluate(quiet)
    second_move = detector.evaluate(fast)

    summary = DailySummaryEngine().generate(
        trade_date=_stamp().date(),
        generated_at=_stamp(15, 30),
        alert_history=[
            {
                "payload_json": json.dumps(
                    {
                        "candidates": [
                            {
                                "code": "600002",
                                "name": "样本600002",
                                "sector": "模拟板块",
                                "price": 10.0,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            }
        ],
        closing_prices={"600002": 10.2},
    )

    return {
        "healthy": _dump_batch(healthy),
        "stopped": _dump_batch(stopped),
        "warming": _dump_batch(warming),
        "replacement": _dump_batch(replacement),
        "stable_first_codes": [candidate.code for candidate in first_stable.candidates],
        "stable_second_codes": [candidate.code for candidate in second_stable.candidates],
        "scheduled_0945": {
            "should_alert": scheduled_0945.should_alert,
            "reason": scheduled_0945.reason,
        },
        "scheduled_1445": {
            "should_alert": scheduled_1445.should_alert,
            "reason": scheduled_1445.reason,
        },
        "first_intraday": {
            "should_alert": first_intraday.should_alert,
            "reason": first_intraday.reason,
        },
        "cooldown": {"should_alert": cooldown.should_alert, "reason": cooldown.reason},
        "after_cooldown": {
            "should_alert": after_cooldown.should_alert,
            "reason": after_cooldown.reason,
        },
        "daily_limit": [
            {"should_alert": item.should_alert, "reason": item.reason} for item in daily_hits
        ],
        "strong_move_first": None
        if first_move is None
        else {
            "codes": list(first_move.triggering_codes),
            "strength": first_move.strength,
            "funds_unconfirmed": first_move.funds_unconfirmed,
        },
        "strong_move_second": None
        if second_move is None
        else {
            "codes": list(second_move.triggering_codes),
            "strength": second_move.strength,
            "funds_unconfirmed": second_move.funds_unconfirmed,
        },
        "summary": summary.as_record(),
    }


def dump_service(tmp: Path) -> dict[str, Any]:
    store = SQLiteStore(tmp / "service.sqlite3")
    store.initialize()
    clock = _stamp()
    service = StockWatcherService(store, clock=lambda: clock, auto_start_session=False)
    engine = CandidateEngine()
    batch = engine.calculate(
        (
            _item("600003"),
            _item("600002"),
            _item("600001", change_pct=1.0, velocity_pct=0.2, sector_strength=0.5),
        ),
        HealthState.HEALTHY,
        CandidateConfig("v0.2", "0.2.0"),
    )
    assert batch is not None
    service.batch = batch
    service.state = HealthState.HEALTHY
    snapshot_id = service._persist_scan_snapshot(clock)  # noqa: SLF001
    public = PublicStateBuilder(store).build(now=clock)
    events: list[dict[str, Any]] = []
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT event_type, payload_json FROM web_events ORDER BY event_id"
        ).fetchall()
        events = [{"event_type": row[0], "payload": json.loads(row[1])} for row in rows]
        snapshot_payload = connection.execute(
            "SELECT payload_json FROM candidate_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
    return _strip_repeat(
        {
            "snapshot_id": snapshot_id,
            "snapshot_trace": json.loads(snapshot_payload[0]) if snapshot_payload else None,
            "public_state": public,
            "events": events,
            "candidate_payload": service._candidate_payload(batch),  # noqa: SLF001
        }
    )


def _diff(left: Any, right: Any, path: str = "") -> list[str]:
    if type(left) is not type(right):
        return [f"{path}: type {type(left).__name__} vs {type(right).__name__}"]
    if isinstance(left, dict):
        keys = set(left) | set(right)
        diffs: list[str] = []
        for key in sorted(keys):
            child = f"{path}.{key}" if path else key
            if key not in left:
                diffs.append(f"{child}: missing on left")
            elif key not in right:
                diffs.append(f"{child}: missing on right")
            else:
                diffs.extend(_diff(left[key], right[key], child))
        return diffs
    if isinstance(left, list):
        if len(left) != len(right):
            return [f"{path}: len {len(left)} vs {len(right)}"]
        diffs = []
        for index, (item_left, item_right) in enumerate(zip(left, right)):
            diffs.extend(_diff(item_left, item_right, f"{path}[{index}]"))
        return diffs
    if left != right:
        return [f"{path}: {left!r} vs {right!r}"]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--label")
    parser.add_argument("--tmp", type=Path)
    parser.add_argument("--compare-left", type=Path)
    parser.add_argument("--compare-right", type=Path)
    args = parser.parse_args()
    if args.compare_left and args.compare_right:
        left = json.loads(args.compare_left.read_text(encoding="utf-8"))
        right = json.loads(args.compare_right.read_text(encoding="utf-8"))
        core_diffs = _diff(left.get("core"), right.get("core"), "core")
        service_diffs = _diff(left.get("service"), right.get("service"), "service")
        payload = {
            "NON_REGRESSION": "PASS" if not core_diffs else "FAIL",
            "core_diff_count": len(core_diffs),
            "core_diffs": core_diffs[:50],
            "service_diff_count": len(service_diffs),
            "service_diffs": service_diffs[:80],
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"NON_REGRESSION={'PASS' if not core_diffs else 'FAIL'}")
        print(args.out)
        return 0 if not core_diffs else 1
    if not args.label or args.tmp is None:
        raise SystemExit("dump mode requires --label and --tmp")
    args.tmp.mkdir(parents=True, exist_ok=True)
    payload = {
        "label": args.label,
        "core": dump_core(),
        "service": dump_service(args.tmp),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
