"""Public dashboard state keeps the latest realtime observation visible."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from stock_watcher.domain import SHANGHAI, HealthState
from stock_watcher.engine import AlertTrigger, Candidate
from stock_watcher.engine.candidates import CandidateBatch
from stock_watcher.runtime import AutomationTaskState, ScanOutcome
from stock_watcher.services.public_state import PublicStateBuilder
from stock_watcher.services.stockwatcher_service import ServiceConfig, StockWatcherService
from stock_watcher.storage import SQLiteStore


def make_batch(source_ts: datetime, prefix: str) -> CandidateBatch:
    candidates = tuple(
        Candidate(
            code=f"6000{index:02d}.SH",
            name=f"{prefix}{index}",
            sector="测试板块",
            sector_code="TEST",
            level="强" if index == 1 else "中",
            score=50.0 - index,
            price_score=20.0,
            sector_score=20.0,
            trend_score=10.0,
            penalty=0.0,
            reasons=("测试",),
            source_ts=source_ts,
            provider_version="test-provider",
            config_version="test-config",
            app_version="test-app",
            price=10.0 + index,
            change_pct=float(index),
            total_score=50.0 - index,
            core_score=40.0,
            is_formal=True,
            is_supplement=False,
        )
        for index in range(1, 4)
    )
    return CandidateBatch(
        source_ts=source_ts,
        generated_at=source_ts,
        health=HealthState.HEALTHY,
        overall_weak=False,
        candidates=candidates,
    )


def test_public_state_falls_back_to_latest_snapshot_when_projection_is_empty(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "state.sqlite3")
    store.initialize()
    builder = PublicStateBuilder(store)
    first_time = datetime(2026, 8, 8, 9, 45, tzinfo=SHANGHAI)

    initial = builder.build(now=first_time)
    assert initial["snapshot_id"] is None
    assert initial["candidates"] == []

    first_id = store.record_batch(make_batch(first_time, "第一批"))
    with store.transaction() as connection:
        store.upsert_public_state(
            connection,
            state_version=1,
            snapshot_id=None,
            source_ts=None,
            payload={
                "service_state": "warming",
                "market_state": "morning",
                "candidates": [],
            },
        )

    previous = builder.build(now=first_time + timedelta(minutes=1))
    assert previous["snapshot_id"] == first_id
    assert previous["candidates_source"] == "last_realtime_snapshot"
    assert [row["name"] for row in previous["candidates"]] == [
        "第一批1",
        "第一批2",
        "第一批3",
    ]
    assert previous["source_ts"] == first_time.isoformat()

    second_time = first_time + timedelta(minutes=5)
    second_id = store.record_batch(make_batch(second_time, "第二批"))
    with store.transaction() as connection:
        store.upsert_public_state(
            connection,
            state_version=2,
            snapshot_id=second_id,
            source_ts=second_time.isoformat(),
            payload={
                "service_state": "healthy",
                "market_state": "morning",
                "candidates": [
                    {
                        "rank": 1,
                        "code": "60001.SH",
                        "name": "第二批1",
                        "level": "强",
                        "is_formal": True,
                        "is_supplement": False,
                        "price": 11.0,
                        "change_pct": 1.0,
                        "sector_name": "测试板块",
                        "sector_type": "industry",
                        "total_score": 49.0,
                    }
                ],
            },
        )

    current = builder.build(now=second_time + timedelta(minutes=1))
    assert current["snapshot_id"] == second_id
    assert current["candidates_source"] == "current_public_state"
    assert current["candidates"][0]["name"] == "第二批1"


def test_healthy_scan_snapshot_is_persisted_and_bound_to_public_state(tmp_path: Path) -> None:
    now = datetime(2026, 8, 8, 10, 0, tzinfo=SHANGHAI)
    store = SQLiteStore(tmp_path / "service.sqlite3")
    store.initialize()
    service = StockWatcherService(store, clock=lambda: now)
    service.batch = make_batch(now, "自动")
    service.state = HealthState.HEALTHY

    snapshot_id = service._persist_scan_snapshot(now)  # noqa: SLF001

    assert snapshot_id is not None
    state = PublicStateBuilder(store).build(now=now)
    assert state["snapshot_id"] == snapshot_id
    assert [row["name"] for row in state["candidates"]] == ["自动1", "自动2", "自动3"]


def test_stopped_fixed_trigger_never_replays_old_batch_as_new_alert(tmp_path: Path) -> None:
    now = datetime(2026, 8, 8, 9, 45, tzinfo=SHANGHAI)
    store = SQLiteStore(tmp_path / "stopped.sqlite3")
    store.initialize()
    service = StockWatcherService(store, clock=lambda: now)
    service.batch = make_batch(now - timedelta(minutes=10), "旧批次")
    service.state = HealthState.STOPPED

    snapshot_id = service._evaluate_alerts(  # noqa: SLF001
        now,
        None,
        forced_fixed=AlertTrigger.SCHEDULED_0945,
    )

    assert snapshot_id is None
    assert store.list_alert_history(now=now, days=1) == []


def test_alert_detail_persists_display_copy_and_delay_flag(tmp_path: Path) -> None:
    now = datetime(2026, 8, 8, 9, 45, tzinfo=SHANGHAI)
    store = SQLiteStore(tmp_path / "alert.sqlite3")
    store.initialize()
    service = StockWatcherService(store, clock=lambda: now)
    service.batch = make_batch(now, "提醒")
    service.state = HealthState.HEALTHY

    service._record_alert(  # noqa: SLF001
        now,
        AlertTrigger.SCHEDULED_0945,
        "scheduled",
        "09:45 观察提醒",
        "当前最新3只",
    )

    row = store.list_alert_history(now=now, days=1)[0]
    detail = json.loads(str(row["detail_json"]))
    assert detail["title"] == "09:45 观察提醒"
    assert detail["subtitle"] == "当前最新3只"
    assert detail["delayed"] is False


def test_first_external_failure_resets_runtime_only_once(tmp_path: Path) -> None:
    now = datetime(2026, 8, 8, 10, 0, tzinfo=SHANGHAI)
    store = SQLiteStore(tmp_path / "recovery.sqlite3")
    store.initialize()
    service = StockWatcherService(store, clock=lambda: now)

    class FailedRuntime:
        reset_calls = 0

        def scan_once(self) -> ScanOutcome:
            return ScanOutcome(
                HealthState.STOPPED,
                "外部数据失败",
                None,
                None,
                None,
                0.1,
                0.0,
                failure_reason="network",
            )

        def reset_for_external_recovery(self) -> None:
            self.reset_calls += 1

    runtime = FailedRuntime()
    service._runtime = runtime  # type: ignore[assignment]  # noqa: SLF001
    service._ensure_runtime = lambda _now: True  # type: ignore[assignment]  # noqa: SLF001
    service._secret_getter = lambda: "configured"  # type: ignore[method-assign]  # noqa: SLF001
    service._universe_is_current = lambda _now: True  # type: ignore[assignment]  # noqa: SLF001

    service._tick_scan(now, force=True, manual_request=True)  # noqa: SLF001
    service._tick_scan(now, force=True, manual_request=True)  # noqa: SLF001

    assert runtime.reset_calls == 1


def test_automatic_tick_skips_while_manual_scan_holds_shared_lock(tmp_path: Path) -> None:
    now = datetime(2026, 8, 8, 10, 0, tzinfo=SHANGHAI)
    service = StockWatcherService(SQLiteStore(tmp_path / "lock.sqlite3"), clock=lambda: now)
    service._scan_lock.acquire()  # noqa: SLF001
    try:
        assert service.tick(now=now).skipped_reason == "scan-in-progress"
    finally:
        service._scan_lock.release()  # noqa: SLF001


class _FailedCoverageRuntime:
    universe = None

    def scan_once(self) -> ScanOutcome:
        return ScanOutcome(
            HealthState.STOPPED,
            "覆盖率不足",
            None,
            None,
            None,
            1.0,
            0.5,
            failure_reason="coverage",
        )

    def reset_for_external_recovery(self) -> None:
        return


def _stub_failed_coverage_scan(service: StockWatcherService) -> None:
    service._runtime = _FailedCoverageRuntime()  # type: ignore[assignment]  # noqa: SLF001
    service._ensure_runtime = lambda _now: True  # type: ignore[assignment]  # noqa: SLF001
    service._secret_getter = lambda: "configured"  # type: ignore[method-assign]  # noqa: SLF001
    service._universe_is_current = lambda _now: True  # type: ignore[assignment]  # noqa: SLF001


def _automation_events(service: StockWatcherService, task_key: str) -> list[dict[str, object]]:
    return [
        event
        for event in service._outbox.read_since(0)  # noqa: SLF001
        if event["event_type"] == "automation.updated" and event["source_id"] == task_key
    ]


def test_fixed_task_succeeds_when_today_alert_exists_despite_failed_scan(tmp_path: Path) -> None:
    now = datetime(2026, 8, 3, 14, 45, 16, tzinfo=SHANGHAI)
    store = SQLiteStore(tmp_path / "fixed-success.sqlite3")
    store.initialize()
    service = StockWatcherService(store, clock=lambda: now)
    service.batch = make_batch(now - timedelta(minutes=1), "跨界")
    service.state = HealthState.HEALTHY
    snapshot_id = service._record_alert(  # noqa: SLF001
        now - timedelta(seconds=8),
        AlertTrigger.SCHEDULED_1445,
        "scheduled",
        "14:45 观察提醒",
        "当前最新3只",
    )
    _stub_failed_coverage_scan(service)

    service.tick(now=now)

    task = store.get_automation_task("2026-08-03:scheduled-14:45")
    assert task is not None
    assert task["state"] == AutomationTaskState.SUCCEEDED.value
    assert task["snapshot_id"] == snapshot_id


def test_fixed_task_fails_when_scan_fails_and_today_alert_is_missing(tmp_path: Path) -> None:
    now = datetime(2026, 8, 3, 14, 45, 16, tzinfo=SHANGHAI)
    store = SQLiteStore(tmp_path / "fixed-fail.sqlite3")
    store.initialize()
    service = StockWatcherService(store, clock=lambda: now)
    service.batch = make_batch(now - timedelta(minutes=1), "旧批次")
    service.state = HealthState.HEALTHY
    _stub_failed_coverage_scan(service)

    service.tick(now=now)

    task = store.get_automation_task("2026-08-03:scheduled-14:45")
    assert task is not None
    assert task["state"] == AutomationTaskState.FAILED.value
    assert store.list_alert_history(now=now, days=1) == []


def test_expired_failed_task_is_not_remarked_on_later_tick(tmp_path: Path) -> None:
    now = datetime(2026, 8, 3, 14, 50, tzinfo=SHANGHAI)
    failed_at = datetime(2026, 8, 3, 14, 48, tzinfo=SHANGHAI)
    store = SQLiteStore(tmp_path / "expire.sqlite3")
    store.initialize()
    service = StockWatcherService(store, clock=lambda: now)
    store.ensure_automation_task(
        {
            "task_key": "2026-08-03:scheduled-14:45",
            "task_type": "scheduled-14:45",
            "trade_date": "2026-08-03",
            "target_at": datetime(2026, 8, 3, 14, 45, tzinfo=SHANGHAI).isoformat(),
            "deadline_at": datetime(2026, 8, 3, 14, 46, 30, tzinfo=SHANGHAI).isoformat(),
            "state": AutomationTaskState.PLANNED.value,
            "updated_at": datetime(2026, 8, 3, 14, 45, tzinfo=SHANGHAI).isoformat(),
            "detail": "等待目标时间。",
        }
    )
    service._mark_task(  # noqa: SLF001
        "2026-08-03:scheduled-14:45",
        state=AutomationTaskState.FAILED,
        now=failed_at,
        detail="超过产品截止时间仍未成功；保留失败证据。",
    )
    saved = store.get_automation_task("2026-08-03:scheduled-14:45")
    assert saved is not None
    events_before = _automation_events(service, "2026-08-03:scheduled-14:45")
    _stub_failed_coverage_scan(service)

    service.tick(now=now)

    updated = store.get_automation_task("2026-08-03:scheduled-14:45")
    assert updated is not None
    assert updated["state"] == AutomationTaskState.FAILED.value
    assert updated["updated_at"] == saved["updated_at"]
    assert _automation_events(service, "2026-08-03:scheduled-14:45") == events_before


def test_summary_artifact_write_failure_logs_warning_and_retries(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 8, 3, 15, 30, tzinfo=SHANGHAI)
    store = SQLiteStore(tmp_path / "summary.sqlite3")
    store.initialize()
    store.record_daily_summary(
        {
            "trade_date": "2026-08-03",
            "generated_at": now.isoformat(),
            "alert_count": 1,
            "top_sectors": [],
            "repeated_candidates": [],
            "closing_performance": [],
            "fund_summary": "资金未确认",
            "health_summary": "本地运行连续",
            "summary_text": "本地总结",
            "version": "daily-summary-local-fallback-v1",
        }
    )
    service = StockWatcherService(
        store,
        config=ServiceConfig(report_dir=tmp_path / "reports"),
        clock=lambda: now,
        auto_start_session=False,
    )

    def boom(_summary: dict[str, object]) -> None:
        raise RuntimeError("disk full")

    service._write_local_summary_report = boom  # type: ignore[assignment]  # noqa: SLF001
    with caplog.at_level(logging.WARNING, logger="stock_watcher.service"):
        assert service.generate_summary(now) is False
    assert service._summary_retry_at == now + timedelta(seconds=60)  # noqa: SLF001
    assert any(
        record.levelno == logging.WARNING
        and "stage=local_report_write" in record.getMessage()
        and record.exc_info is not None
        for record in caplog.records
    )
