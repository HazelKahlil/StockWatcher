"""Public dashboard state keeps the latest realtime observation visible."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from stock_watcher.domain import SHANGHAI, HealthState
from stock_watcher.engine import AlertTrigger, Candidate
from stock_watcher.engine.candidates import CandidateBatch
from stock_watcher.runtime import ScanOutcome
from stock_watcher.services.public_state import PublicStateBuilder
from stock_watcher.services.stockwatcher_service import StockWatcherService
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
