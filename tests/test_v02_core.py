from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from stock_watcher.domain import SHANGHAI, CandidateInput, HealthState, Security
from stock_watcher.engine import (
    AlertPolicy,
    AlertPolicyConfig,
    AlertTrigger,
    CandidateConfig,
    CandidateEngine,
    ReplaySchedule,
)
from stock_watcher.engine.candidates import CandidateBatch
from stock_watcher.providers.tushare import Tushare15000Provider
from stock_watcher.runtime import TushareV1Runtime
from stock_watcher.security import MemoryCredentialStore
from stock_watcher.storage import SQLiteStore
from stock_watcher.ui.tushare_v1_session import TushareV1Session

ROOT = Path(__file__).resolve().parents[1]


def stamp(minute: int = 45) -> datetime:
    return datetime(2026, 7, 23, 9, minute, tzinfo=SHANGHAI)


def item(code: str, **changes: object) -> CandidateInput:
    values: dict[str, object] = {
        "security": Security(code, f"样本{code}", "SH"),
        "price": 10.0,
        "change_pct": 6.0,
        "velocity_pct": 2.5,
        "sector": "模拟板块",
        "sector_strength": 3.0,
        "trend_3d_pct": 1.0,
        "source_ts": stamp(),
        "received_ts": stamp(),
        "provider_version": "replay-v1",
        "config_version": "v0.2",
    }
    values.update(changes)
    return CandidateInput(**values)  # type: ignore[arg-type]


def batch(*inputs: CandidateInput) -> CandidateBatch | None:
    return CandidateEngine().calculate(
        inputs, HealthState.HEALTHY, CandidateConfig("v0.2", "0.2.0")
    )


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"security": Security("430001", "北交", "BJ")}, "北交所"),
        ({"is_st": True}, "ST"),
        ({"is_delisting": True}, "退市整理"),
        ({"is_suspended": True}, "停牌"),
        ({"is_limit_up": True}, "一字涨停"),
        ({"is_new_or_corporate_action": True}, "新股/复牌/除权当日"),
        ({"is_complete": False}, "数据不完整"),
    ],
)
def test_locked_universe_exclusions(changes: dict[str, object], reason: str) -> None:
    assert item("600001", **changes).exclusion_reason == reason


def test_candidate_engine_is_deterministic_stable_and_fills_nearest() -> None:
    inputs = (
        item("600003"),
        item("600002"),
        item("600001", change_pct=1.0, velocity_pct=0.2, sector_strength=0.5),
    )
    first, second = batch(*inputs), batch(*inputs)
    assert first == second
    assert first is not None
    assert [candidate.code for candidate in first.candidates] == ["600002", "600003", "600001"]
    assert first.candidates[0].level == "强"
    assert first.candidates[-1].level == "近"
    assert "板块未通过，最高仅近" in first.candidates[-1].reasons
    assert first.fund_module == "unavailable"


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4])
def test_signal_count_never_fabricates_and_caps_at_three(count: int) -> None:
    result = batch(*(item(f"6000{number:02d}") for number in range(count)))
    assert result is not None
    assert len(result.candidates) == min(count, 3)
    assert result.overall_weak is (count < 3)


def test_stopped_and_warming_produce_no_new_batch() -> None:
    engine = CandidateEngine()
    config = CandidateConfig("v0.2", "0.2.0")
    assert engine.calculate((item("600001"),), HealthState.STOPPED, config) is None
    assert engine.calculate((item("600001"),), HealthState.WARMING, config) is None


def test_schedule_uses_injected_replay_clock_only() -> None:
    schedule = ReplaySchedule()
    assert schedule.due(stamp())
    assert schedule.due(datetime(2026, 7, 23, 14, 45, tzinfo=SHANGHAI))
    assert not schedule.due(datetime(2026, 7, 23, 14, 50, tzinfo=SHANGHAI))
    assert not schedule.due(stamp(44))


def test_alert_policy_binds_debounce_to_relation_and_fresh_source_cycles() -> None:
    first = batch(item("600001"), item("600002"), item("600003"))
    replacement = batch(item("600001"), item("600002"), item("600004"))
    assert first is not None and replacement is not None
    policy = AlertPolicy(AlertPolicyConfig(replacement_cycles=2, replacement_margin=1.0))
    now = stamp()
    assert policy.decide(first, now, AlertTrigger.INTRADAY).should_alert
    same_source = batch(
        item("600001", source_ts=stamp(46)),
        item("600002", source_ts=stamp(46)),
        item("600004", source_ts=stamp(46)),
    )
    assert same_source is not None
    assert (
        policy.decide(same_source, now + timedelta(minutes=6), AlertTrigger.INTRADAY).reason
        == "replacement-debounce"
    )
    assert (
        policy.decide(same_source, now + timedelta(minutes=12), AlertTrigger.INTRADAY).reason
        == "stale-source"
    )
    other_replacement = batch(
        item("600001", source_ts=stamp(47)),
        item("600002", source_ts=stamp(47)),
        item("600005", source_ts=stamp(47)),
    )
    assert other_replacement is not None
    assert (
        policy.decide(other_replacement, now + timedelta(minutes=18), AlertTrigger.INTRADAY).reason
        == "replacement-debounce"
    )
    same_relation_fresh = batch(
        item("600001", source_ts=stamp(48)),
        item("600002", source_ts=stamp(48)),
        item("600005", source_ts=stamp(48)),
    )
    assert same_relation_fresh is not None
    assert policy.decide(
        same_relation_fresh, now + timedelta(minutes=24), AlertTrigger.INTRADAY
    ).should_alert


def test_alert_policy_resets_pending_replacement_when_top_three_recovers() -> None:
    baseline = batch(item("600001"), item("600002"), item("600003"))
    first_replacement = batch(
        item("600001", source_ts=stamp(46)),
        item("600002", source_ts=stamp(46)),
        item("600004", source_ts=stamp(46)),
    )
    recovered_baseline = batch(
        item("600001", source_ts=stamp(47)),
        item("600002", source_ts=stamp(47)),
        item("600003", source_ts=stamp(47)),
    )
    second_replacement = batch(
        item("600001", source_ts=stamp(48)),
        item("600002", source_ts=stamp(48)),
        item("600004", source_ts=stamp(48)),
    )
    confirmed_replacement = batch(
        item("600001", source_ts=stamp(49)),
        item("600002", source_ts=stamp(49)),
        item("600004", source_ts=stamp(49)),
    )
    assert (
        baseline is not None
        and first_replacement is not None
        and recovered_baseline is not None
        and second_replacement is not None
        and confirmed_replacement is not None
    )
    policy = AlertPolicy(AlertPolicyConfig(replacement_cycles=2, replacement_margin=1.0))
    now = stamp()

    assert policy.decide(baseline, now, AlertTrigger.INTRADAY).should_alert
    assert (
        policy.decide(first_replacement, now + timedelta(minutes=6), AlertTrigger.INTRADAY).reason
        == "replacement-debounce"
    )
    assert (
        policy.decide(recovered_baseline, now + timedelta(minutes=12), AlertTrigger.INTRADAY).reason
        == "unchanged"
    )
    assert (
        policy.decide(recovered_baseline, now + timedelta(minutes=18), AlertTrigger.INTRADAY).reason
        == "stale-source"
    )
    assert (
        policy.decide(second_replacement, now + timedelta(minutes=24), AlertTrigger.INTRADAY).reason
        == "replacement-debounce"
    )
    assert (
        policy.decide(recovered_baseline, now + timedelta(minutes=30), AlertTrigger.INTRADAY).reason
        == "unchanged"
    )
    assert (
        policy.decide(second_replacement, now + timedelta(minutes=36), AlertTrigger.INTRADAY).reason
        == "replacement-debounce"
    )
    assert policy.decide(
        confirmed_replacement, now + timedelta(minutes=42), AlertTrigger.INTRADAY
    ).should_alert


def test_alert_policy_counts_only_intraday_batches_and_resets_daily() -> None:
    policy = AlertPolicy(AlertPolicyConfig(replacement_cycles=1, daily_limit=3))
    now = stamp()

    def fresh(code: str, minute: int, day_offset: int = 0) -> CandidateBatch:
        source_ts = stamp(minute) + timedelta(days=day_offset)
        result = batch(
            item(code, source_ts=source_ts),
            item("600002", source_ts=source_ts),
            item("600003", source_ts=source_ts),
        )
        assert result is not None
        return result

    assert policy.decide(fresh("600001", 45), now, AlertTrigger.SCHEDULED_0945).should_alert
    assert policy.decide(
        fresh("600004", 46), now + timedelta(minutes=15), AlertTrigger.INTRADAY
    ).should_alert
    assert policy.decide(
        fresh("600005", 47), now + timedelta(hours=5, minutes=5), AlertTrigger.SCHEDULED_1450
    ).should_alert
    assert policy.decide(
        fresh("600006", 48), now + timedelta(hours=5, minutes=10), AlertTrigger.INTRADAY
    ).should_alert
    assert policy.decide(
        fresh("600007", 49), now + timedelta(hours=5, minutes=16), AlertTrigger.INTRADAY
    ).should_alert
    assert (
        policy.decide(
            fresh("600008", 50), now + timedelta(hours=5, minutes=22), AlertTrigger.INTRADAY
        ).reason
        == "daily-limit"
    )
    assert policy.decide(
        fresh("600009", 51, day_offset=1), now + timedelta(days=1), AlertTrigger.INTRADAY
    ).should_alert


def test_sqlite_traceability_immutable_config_and_rollback(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "watcher.sqlite3")
    store.initialize()
    store.record_config_version("v0.2", "test", '{"seed": 7}')
    with pytest.raises(FileExistsError):
        store.record_config_version("v0.2", "test", "{}")
    result = batch(item("600001"), item("600002"), item("600003"))
    assert result is not None
    snapshot_id = store.record_batch(result)
    store.record_alert_event(snapshot_id, stamp().isoformat(), "changed", "desktop")
    store.record_health_metric(
        {
            "source_ts": stamp().isoformat(),
            "received_ts": stamp().isoformat(),
            "state": "HEALTHY",
            "provider_version": "replay-v1",
            "config_version": "v0.2",
            "detail": "simulated",
        }
    )
    with store.connect() as connection:
        payload = connection.execute("SELECT payload_json FROM candidate_snapshots").fetchone()[0]
        assert "reasons" in payload and "price_score" in payload and "fund_module" in payload
        assert connection.execute("SELECT COUNT(*) FROM alert_events").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM health_metrics").fetchone() == (1,)
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    backup = store.backup(tmp_path / "watcher.backup.sqlite3")
    store.put_note("after_backup", "discard")
    store.rollback(backup)
    assert store.get_note("after_backup") is None


def test_sqlite_transaction_rolls_back_and_secret_like_channel_is_rejected(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "watcher.sqlite3")
    with pytest.raises(Exception):
        store.apply_transaction(
            [("INSERT INTO notes VALUES (?, ?)", ("safe", "yes")), ("INVALID SQL", ())]
        )
    assert store.get_note("safe") is None
    with pytest.raises(ValueError, match="credentials"):
        store.record_alert_event(1, stamp().isoformat(), "changed", "token=never-store")


def test_corrupt_database_switches_to_read_only_degradation(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.sqlite3"
    path.write_text("not sqlite", encoding="utf-8")
    store = SQLiteStore(path)
    with pytest.raises(Exception):
        store.initialize()
    assert store.read_only


def test_sqlite_explicit_v5_to_v6_migration_is_idempotent(tmp_path: Path) -> None:
    empty_store = SQLiteStore(tmp_path / "empty.sqlite3")
    empty_store.initialize()
    with empty_store.connect() as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (10,)

    path = tmp_path / "watcher.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_version VALUES (5, '2026-08-06T09:45:00+08:00')")
        SQLiteStore._apply_v1_schema(connection)
        SQLiteStore._apply_v2_migration(connection)
        SQLiteStore._apply_v3_migration(connection)
        SQLiteStore._apply_v4_migration(connection)
        SQLiteStore._apply_v5_migration(connection)
    store = SQLiteStore(path)
    store.initialize()
    store.initialize()
    with store.connect() as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (10,)
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(runtime_sessions)")
        }
        assert {
            "last_sleep_at",
            "last_wake_at",
            "previous_session_id",
            "previous_unclean_exit",
            "watchdog_restart_count",
        } <= columns
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'runtime_events'"
        ).fetchone() == ("runtime_events",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert path.with_suffix(".sqlite3.pre-v6.bak").exists()


def test_runtime_session_and_scan_attempt_lifecycle_is_auditable(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "runtime.sqlite3")
    store.start_runtime_session(
        session_id="session-a",
        pid=101,
        ppid=1,
        app_path="/Applications/StockWatcher.app",
        source_commit="commit-a",
        started_at="2026-08-06T09:20:00+08:00",
    )
    store.record_runtime_event(
        session_id="session-a",
        occurred_at="2026-08-06T09:21:00+08:00",
        event_type="sleep_detected",
        detail={"reason": "system-suspend"},
    )
    store.heartbeat_runtime_session("session-a", "2026-08-06T09:22:00+08:00")
    store.start_scan_attempt(
        attempt_id="attempt-a",
        session_id="session-a",
        started_at="2026-08-06T09:23:00+08:00",
        operation="automatic",
        thread_name="scan-worker",
        timer_active=True,
    )
    store.heartbeat_scan_attempt("attempt-a", "2026-08-06T09:23:30+08:00")
    store.finish_scan_attempt(
        "attempt-a",
        "2026-08-06T09:24:00+08:00",
        state="completed",
        detail="healthy",
    )
    store.end_runtime_session(
        "session-a",
        "2026-08-06T09:25:00+08:00",
        exit_reason="menu_quit",
        graceful_exit=True,
    )
    session = store.get_runtime_session("session-a")
    assert session is not None
    assert session["last_sleep_at"] == "2026-08-06T09:21:00+08:00"
    assert session["graceful_exit"] == 1
    assert store.list_scan_attempts(session_id="session-a")[0]["state"] == "completed"
    assert store.list_runtime_events("session-a")[0]["event_type"] == "sleep_detected"


def test_next_runtime_session_marks_previous_session_unclean(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "runtime.sqlite3")
    store.start_runtime_session(
        session_id="session-a",
        pid=101,
        ppid=1,
        app_path="/Applications/StockWatcher.app",
        source_commit="commit-a",
        started_at="2026-08-06T09:20:00+08:00",
    )
    store.start_runtime_session(
        session_id="session-b",
        pid=102,
        ppid=1,
        app_path="/Applications/StockWatcher.app",
        source_commit="commit-b",
        started_at="2026-08-06T09:30:00+08:00",
    )
    previous = store.get_runtime_session("session-a")
    current = store.get_runtime_session("session-b")
    assert previous is not None and current is not None
    assert previous["exit_reason"] == "unclean_exit"
    assert current["previous_session_id"] == "session-a"
    assert current["previous_unclean_exit"] == 1


def test_sqlite_migration_failure_rolls_back_and_degrades_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "watcher.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_version VALUES (1, 'old')")
        connection.execute("CREATE TABLE notes (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO notes VALUES ('v1-data', 'preserved')")

    def fail_after_partial_table(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE should_rollback (id INTEGER PRIMARY KEY)")
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(SQLiteStore, "_apply_v2_migration", staticmethod(fail_after_partial_table))
    store = SQLiteStore(path)
    with pytest.raises(RuntimeError, match="injected migration failure"):
        store.initialize()
    assert store.read_only
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (1,)
        assert connection.execute("SELECT value FROM notes WHERE key = 'v1-data'").fetchone() == (
            "preserved",
        )
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'should_rollback'"
            ).fetchone()
            is None
        )


def test_tushare_v1_session_wires_runtime_lifecycle(tmp_path: Path) -> None:
    """Session creation starts a runtime session; heartbeat and shutdown persist."""
    import os
    from zoneinfo import ZoneInfo

    now = datetime(2026, 8, 6, 9, 20, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    session = TushareV1Session(
        tmp_path / "lifecycle.sqlite3",
        credential_store=MemoryCredentialStore(),
        runtime_factory=lambda _settings, _store: (
            cast("TushareV1Runtime", object()),
            cast("Tushare15000Provider", object()),
        ),
        clock=lambda: now,
    )
    try:
        session.heartbeat(now=now + timedelta(minutes=1))
        session.record_platform_event(
            "sleep_detected", now=now + timedelta(minutes=2), detail={"reason": "test"}
        )
        persisted = session.store.get_runtime_session(session._runtime_session_id)
        assert persisted is not None
        assert persisted["pid"] == os.getpid()
        assert persisted["graceful_exit"] == 0
        assert persisted["last_heartbeat_at"].startswith("2026-08-06T09:21")
        assert session.store.list_runtime_events(session._runtime_session_id)[0][
            "event_type"
        ] == "sleep_detected"
    finally:
        session.shutdown(exit_reason="menu_quit")
    ended = session.store.get_runtime_session(session._runtime_session_id)
    assert ended is not None
    assert ended["graceful_exit"] == 1
    assert ended["exit_reason"] == "menu_quit"


def test_sleep_cancels_active_scan_and_records_sleep_event(tmp_path: Path) -> None:
    """mark_sleep persists sleep_detected and voids the in-flight scan attempt."""
    now = datetime(2026, 8, 6, 10, 30, 0, tzinfo=SHANGHAI)
    session = TushareV1Session(
        tmp_path / "sleep.sqlite3",
        credential_store=MemoryCredentialStore(),
        runtime_factory=lambda _settings, _store: (
            cast(TushareV1Runtime, object()),
            cast(Tushare15000Provider, object()),
        ),
        clock=lambda: now,
    )
    try:
        attempt_id = session._begin_scan_attempt(now=now, operation="automatic")
        assert attempt_id is not None
        session.mark_sleep(now=now, reason="system-suspend")
        attempts = session.store.list_scan_attempts(session_id=session._runtime_session_id)
        assert attempts[0]["state"] == "sleep_interrupted"
        events = session.store.list_runtime_events(session._runtime_session_id)
        assert events[0]["event_type"] == "sleep_detected"
        persisted = session.store.get_runtime_session(session._runtime_session_id)
        assert persisted is not None
        assert persisted["last_sleep_at"].startswith("2026-08-06T10:30")
    finally:
        session.shutdown(exit_reason="menu_quit")


def test_scan_stall_records_event_and_enters_recovery(tmp_path: Path) -> None:
    """A >90s trading-hours gap without sleep triggers scan_stalled recovery."""
    now = datetime(2026, 8, 6, 10, 30, 0, tzinfo=SHANGHAI)
    session = TushareV1Session(
        tmp_path / "stall.sqlite3",
        credential_store=MemoryCredentialStore(),
        runtime_factory=lambda _settings, _store: (
            cast(TushareV1Runtime, object()),
            cast(Tushare15000Provider, object()),
        ),
        clock=lambda: now,
    )
    try:
        session._runtime = cast(TushareV1Runtime, object())
        session.last_scan_succeeded_at = now - timedelta(minutes=3)
        session._run(force=True, manual_request=False)
        events = session.store.list_runtime_events(session._runtime_session_id)
        assert events[0]["event_type"] == "scan_stalled"
        assert session._platform_recovery_reason is not None
        assert session.state is HealthState.WARMING
    finally:
        session.shutdown(exit_reason="menu_quit")


def _seed_healthy_scan_run(
    store: SQLiteStore,
    trade_date: str,
    times: list[str],
) -> None:
    for completed in times:
        store.record_scan_run(
            {
                "started_at": completed,
                "completed_at": completed,
                "trigger_type": "automatic",
                "task_key": None,
                "health": HealthState.HEALTHY.value,
                "detail": "正常",
                "stable_batch_json": '{"candidates": []}',
                "audit_json": "{}",
            }
        )


def test_summary_scheduler_runs_decoupled_from_scan_loop(tmp_path: Path) -> None:
    """check_automation_tasks reaches a terminal state without any scan."""
    now = datetime(2026, 8, 6, 15, 30, 0, tzinfo=SHANGHAI)
    session = TushareV1Session(
        tmp_path / "summary.sqlite3",
        credential_store=MemoryCredentialStore(),
        runtime_factory=lambda _settings, _store: (
            cast(TushareV1Runtime, object()),
            cast(Tushare15000Provider, object()),
        ),
        clock=lambda: now,
    )
    try:
        _seed_healthy_scan_run(
            session.store,
            "2026-08-06",
            ["2026-08-06T09:50:00+08:00", "2026-08-06T09:50:10+08:00"],
        )
        session.check_automation_tasks(now=now)
        task = session.store.get_automation_task("2026-08-06:summary-15:30")
        assert task is not None
        assert task["state"] == "succeeded"
        summary = session.store.get_daily_summary("2026-08-06")
        assert summary is not None
        assert summary["catch_up"] == 0
        assert "最长无扫描间隔" in summary["health_summary"]
    finally:
        session.shutdown(exit_reason="menu_quit")


def test_summary_catch_up_marks_catch_up_flag(tmp_path: Path) -> None:
    """A 15:32 launch catches up and records catch_up=true."""
    now = datetime(2026, 8, 6, 15, 32, 0, tzinfo=SHANGHAI)
    session = TushareV1Session(
        tmp_path / "catchup.sqlite3",
        credential_store=MemoryCredentialStore(),
        runtime_factory=lambda _settings, _store: (
            cast(TushareV1Runtime, object()),
            cast(Tushare15000Provider, object()),
        ),
        clock=lambda: now,
    )
    try:
        _seed_healthy_scan_run(
            session.store,
            "2026-08-06",
            ["2026-08-06T09:50:00+08:00"],
        )
        session.check_automation_tasks(now=now)
        summary = session.store.get_daily_summary("2026-08-06")
        assert summary is not None
        assert summary["catch_up"] == 1
        task = session.store.get_automation_task("2026-08-06:summary-15:30")
        assert task is not None
        assert task["state"] == "succeeded"
        assert "catch_up=true" in str(task["detail"])
    finally:
        session.shutdown(exit_reason="menu_quit")


def test_alert_policy_global_batch_cooldown_blocks_rapid_batches() -> None:
    """Two intraday batches must be at least 5 minutes apart."""
    first = batch(item("600001"), item("600002"), item("600003"))
    second = batch(
        item("600004", source_ts=stamp(46)),
        item("600005", source_ts=stamp(46)),
        item("600006", source_ts=stamp(46)),
    )
    assert first is not None and second is not None
    policy = AlertPolicy(AlertPolicyConfig(replacement_cycles=2, replacement_margin=1.0))
    now = stamp()
    assert policy.decide(
        first, now, AlertTrigger.INTRADAY,
        strong_movement=True, triggering_codes=("600001",), event_strength=1.0,
    ).should_alert
    decision = policy.decide(
        second, now + timedelta(minutes=1), AlertTrigger.INTRADAY,
        strong_movement=True, triggering_codes=("600004",), event_strength=1.0,
    )
    assert decision.reason == "global-cooldown"
    assert policy.decide(
        second, now + timedelta(minutes=6), AlertTrigger.INTRADAY,
        strong_movement=True, triggering_codes=("600004",), event_strength=1.2,
    ).should_alert


def test_feature_readiness_blocks_warmup_strong_alert(tmp_path: Path) -> None:
    """A warming baseline must never fire an intraday anomaly alert."""
    from types import SimpleNamespace

    from stock_watcher.engine import StrongMovementEvent

    now = datetime(2026, 8, 6, 10, 5, 0, tzinfo=SHANGHAI)
    session = TushareV1Session(
        tmp_path / "warmup.sqlite3",
        credential_store=MemoryCredentialStore(),
        runtime_factory=lambda _settings, _store: (
            cast(TushareV1Runtime, object()),
            cast(Tushare15000Provider, object()),
        ),
        clock=lambda: now,
    )
    try:
        session.batch = batch(item("600001"), item("600002"), item("600003"))
        assert session.batch is not None
        event = StrongMovementEvent(
            triggering_codes=("600001",),
            strength=1.5,
            funds_unconfirmed=True,
        )
        audit = SimpleNamespace(warmup_state="warming", display_velocity_ready=False, rows=())
        assert (
            session._evaluate_alerts(now, event, selection_audit=audit, scan_run_id=7)
            is None
        )
        assert session.store.list_alert_history(now=now, days=1) == []
    finally:
        session.shutdown(exit_reason="menu_quit")


def test_strong_alert_fires_with_full_detail_when_ready(tmp_path: Path) -> None:
    """A ready baseline stores complete audit detail in alert_events."""
    import json as json_module
    from types import SimpleNamespace

    from stock_watcher.engine import StrongMovementEvent

    now = datetime(2026, 8, 6, 10, 5, 0, tzinfo=SHANGHAI)
    session = TushareV1Session(
        tmp_path / "ready.sqlite3",
        credential_store=MemoryCredentialStore(),
        runtime_factory=lambda _settings, _store: (
            cast(TushareV1Runtime, object()),
            cast(Tushare15000Provider, object()),
        ),
        clock=lambda: now,
    )
    try:
        session.batch = batch(item("600001"), item("600002"), item("600003"))
        assert session.batch is not None
        event = StrongMovementEvent(
            triggering_codes=("600001",),
            strength=1.5,
            funds_unconfirmed=True,
        )
        audit = SimpleNamespace(warmup_state="ready", display_velocity_ready=True, rows=())
        snapshot_id = session._evaluate_alerts(
            now, event, selection_audit=audit, scan_run_id=7
        )
        assert snapshot_id is not None
        rows = session.store.list_alert_history(now=now, days=1)
        assert rows and rows[0]["trigger_type"] == "intraday"
        detail = json_module.loads(str(rows[0]["detail_json"]))
        assert detail["trigger_symbol"] == "600001"
        assert detail["trigger_time"].startswith("2026-08-06T10:05")
        assert detail["feature_readiness"] == "ready"
        assert detail["source_scan_id"] == 7
        assert detail["cooldown_decision"] == "strong-movement"
    finally:
        session.shutdown(exit_reason="menu_quit")


def test_export_selection_audit_generates_full_machine_readable_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The export tool produces all nine required audit files."""
    import importlib.util
    import sys as sys_module

    db = tmp_path / "audit.sqlite3"
    store = SQLiteStore(db)
    now = datetime(2026, 8, 6, 10, 5, 0, tzinfo=SHANGHAI)
    base = batch(item("600001"), item("600002"), item("600003"))
    assert base is not None
    snapshot_id = store.record_batch(base)
    store.record_alert_event(
        snapshot_id,
        now.isoformat(),
        "strong-movement",
        "macos-desktop",
        trigger_type="intraday",
        detail={"trigger_symbol": "600001", "feature_readiness": "ready"},
    )
    store.record_scan_run(
        {
            "started_at": now.isoformat(),
            "completed_at": now.isoformat(),
            "trigger_type": "automatic",
            "task_key": None,
            "health": HealthState.HEALTHY.value,
            "detail": "正常",
            "stable_batch_json": '{"candidates": []}',
            "audit_json": (
                '{"warmup_state": "ready", "raw_codes": ["600001", "600002", "600003"], '
                '"stable_codes": ["600001", "600002", "600003"], '
                '"rows": [{"raw_rank": 1, "code": "600001", "name": "样本600001", '
                '"sector": "模拟板块", "sector_type": "industry", "total_score": 50.0, '
                '"level": "强", "is_formal": true, "velocity_available": true, '
                '"velocity_1m_pct": 1.2, "selected_raw": true, "selected_stable": true, '
                '"decision": "displayed"}]}'
            ),
        }
    )
    store.start_runtime_session(
        session_id="session-x",
        pid=101,
        ppid=1,
        app_path="/Applications/StockWatcher.app",
        source_commit="commit-a",
        started_at=now.isoformat(),
    )
    store.record_runtime_event(
        session_id="session-x",
        occurred_at=now.isoformat(),
        event_type="sleep_detected",
        detail={"reason": "test"},
    )
    store.ensure_automation_task(
        {
            "task_key": "2026-08-06:summary-15:30",
            "task_type": "summary-15:30",
            "trade_date": "2026-08-06",
            "target_at": "2026-08-06T15:30:00+08:00",
            "deadline_at": "2026-08-06T17:30:00+08:00",
            "state": "planned",
            "updated_at": now.isoformat(),
            "detail": "等待目标时间。",
        }
    )

    script = ROOT / "scripts" / "export_selection_audit.py"
    module_name = "stockwatcher_test_export_audit"
    spec = importlib.util.spec_from_file_location(module_name, script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys_module.modules[module_name] = module
    assert isinstance(module, ModuleType)
    spec.loader.exec_module(module)

    output = tmp_path / "export"
    monkeypatch.setattr(
        sys_module,
        "argv",
        ["export", str(db), "2026-08-06", str(output)],
    )
    assert module.main() == 0
    expected = [
        "scan-runs.json",
        "scan-runs.csv",
        "candidate-audit.csv",
        "raw-top20.csv",
        "raw-top20.json",
        "raw-top3.csv",
        "raw-top3.json",
        "stable-top3-timeline.csv",
        "excluded-candidates.csv",
        "automation-tasks.csv",
        "runtime-sessions.csv",
        "scheduler-events.csv",
        "cache-status.csv",
        "alert-events.csv",
    ]
    for name in expected:
        assert (output / name).is_file(), name
    # The final exporter must emit a true score-order Top20 (up to 20 rows per
    # scan) and an explicit 3-row Raw Top3 instead of the legacy raw_codes[:20]
    # defect. This seeded fixture has a single ranked candidate; the dedicated
    # test_exporter_true_top20 test covers the full 20-row contract.
    import csv as _csv

    with (
        (output / "raw-top20.csv").open(encoding="utf-8", newline="") as top20_file,
        (output / "raw-top3.csv").open(encoding="utf-8", newline="") as top3_file,
    ):
        top20_rows = list(_csv.DictReader(top20_file))
        top3_rows = list(_csv.DictReader(top3_file))
    assert 1 <= len(top20_rows) <= 20
    assert top20_rows[0]["rank"] == "1"
    assert len(top3_rows) == 3
    assert [row["rank"] for row in top3_rows] == ["1", "2", "3"]
    alert_csv = (output / "alert-events.csv").read_text(encoding="utf-8")
    assert "600001" in alert_csv
    assert (output / "stable-top3-timeline.csv").read_text(encoding="utf-8").count("600001") >= 1


def test_sqlite_auto_recovers_damaged_file_from_backup(tmp_path: Path) -> None:
    """A non-SQLite database file is replaced by the newest valid backup."""
    path = tmp_path / "watcher.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_version VALUES (5, '2026-08-06T09:45:00+08:00')")
        SQLiteStore._apply_v1_schema(connection)
        SQLiteStore._apply_v2_migration(connection)
        SQLiteStore._apply_v3_migration(connection)
        SQLiteStore._apply_v4_migration(connection)
        SQLiteStore._apply_v5_migration(connection)
    store = SQLiteStore(path)
    store.initialize()  # v5 -> v6, creates .pre-v6.bak
    with store.connect() as connection:
        connection.execute("INSERT INTO notes (key, value) VALUES ('probe', 'kept')")

    with path.open("r+b") as handle:
        handle.write(b"lxml._elementpath, lxml.etree, numpy (total: 69)")
    assert path.read_bytes()[:16] != b"SQLite format 3\x00"

    recovered = SQLiteStore(path)
    recovered.initialize()
    assert recovered.last_recovery is not None
    assert recovered.last_recovery["source_backup"] == "watcher.sqlite3.pre-v6.bak"
    with recovered.connect() as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (10,)
        assert connection.execute(
            "SELECT value FROM notes WHERE key = 'probe'"
        ).fetchone() is None
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert path.with_suffix(".sqlite3.corrupt").exists()


def test_sqlite_auto_recovers_valid_header_page_corruption_from_configured_backup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "watcher.sqlite3"
    store = SQLiteStore(path)
    store.initialize()
    for index in range(100):
        store.put_note(f"durable-{index}", "x" * 80)
    backup = tmp_path / "backups" / "stockwatcher-20260813" / path.name
    store.backup(backup)
    store.close()

    # Keep the SQLite header magic but damage a data page.  This is the shape
    # of corruption that a magic-byte-only recovery check cannot detect.
    with path.open("r+b") as handle:
        handle.seek(4096)
        handle.write(b"\x00" * 4096)

    recovered = SQLiteStore(
        path,
        recovery_backup_dirs=(backup.parent.parent,),
    )
    recovered.initialize()

    assert recovered.last_recovery is not None
    assert recovered.last_recovery["source_backup"] == str(backup)
    with recovered.connect() as connection:
        assert connection.execute(
            "SELECT value FROM notes WHERE key = 'durable-99'"
        ).fetchone() == ("x" * 80,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert path.with_suffix(".sqlite3.corrupt").exists()


def test_sqlite_auto_recovery_prefers_nested_admin_backup_over_migration_backup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "stockwatcher.db"
    path.parent.mkdir()
    store = SQLiteStore(path)
    store.initialize()
    store.put_note("generation", "migration-backup")
    migration_backup = path.with_suffix(".db.pre-v9.bak")
    store.backup(migration_backup)

    store.put_note("generation", "admin-backup")
    admin_backup = (
        tmp_path
        / "backups"
        / "predeploy-20260813"
        / "stockwatcher-20260813T190010Z"
        / "stockwatcher.sqlite3"
    )
    store.backup(admin_backup)
    store.close()

    # Make the older migration backup appear newer than the live database but
    # keep the admin snapshot newest, matching the production directory shape.
    migration_mtime = admin_backup.stat().st_mtime - 10
    os.utime(migration_backup, (migration_mtime, migration_mtime))
    path.write_bytes(b"not a sqlite database")

    recovered = SQLiteStore(
        path,
        recovery_backup_dirs=(tmp_path / "backups",),
    )
    recovered.initialize()

    assert recovered.last_recovery is not None
    assert recovered.last_recovery["source_backup"] == str(admin_backup)
    with recovered.connect() as connection:
        assert connection.execute(
            "SELECT value FROM notes WHERE key = 'generation'"
        ).fetchone() == ("admin-backup",)


def test_sqlite_recovery_closes_existing_connection_before_replacing_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "watcher.sqlite3"
    store = SQLiteStore(path)
    store.initialize()
    store.put_note("durable", "from-backup")
    backup = tmp_path / "backups" / "stockwatcher" / path.name
    store.backup(backup)

    # Keep the store's thread-local connection alive while replacing the file;
    # recovery must not continue using the quarantined inode.
    path.write_bytes(b"not a sqlite database")
    store.initialize()

    with store.connect() as connection:
        assert connection.execute(
            "SELECT value FROM notes WHERE key = 'durable'"
        ).fetchone() == ("from-backup",)
