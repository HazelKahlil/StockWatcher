from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

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
from stock_watcher.storage import SQLiteStore


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
    assert schedule.due(datetime(2026, 7, 23, 14, 50, tzinfo=SHANGHAI))
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


def test_sqlite_explicit_v1_to_v2_migration_is_idempotent(tmp_path: Path) -> None:
    empty_store = SQLiteStore(tmp_path / "empty.sqlite3")
    empty_store.initialize()
    with empty_store.connect() as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (2,)

    path = tmp_path / "watcher.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_version VALUES (1, '2026-07-23T09:45:00+08:00')")
        connection.execute("CREATE TABLE notes (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO notes VALUES ('v1-data', 'preserved')")
    store = SQLiteStore(path)
    store.initialize()
    store.initialize()
    with store.connect() as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (2,)
        assert connection.execute("SELECT value FROM notes WHERE key = 'v1-data'").fetchone() == (
            "preserved",
        )
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"config_versions", "candidate_snapshots", "alert_events", "health_metrics"} <= tables
    assert path.with_suffix(".sqlite3.pre-v2.bak").exists()


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
