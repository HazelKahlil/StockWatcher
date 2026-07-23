from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from stock_watcher.domain import SHANGHAI, CandidateInput, HealthState, Security
from stock_watcher.engine import (
    AlertPolicy,
    AlertPolicyConfig,
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


def test_alert_policy_cooldown_unchanged_debounce_limit_and_day_reset() -> None:
    first = batch(item("600001"), item("600002"), item("600003"))
    replacement = batch(item("600001"), item("600002"), item("600004"))
    assert first is not None and replacement is not None
    policy = AlertPolicy(AlertPolicyConfig(replacement_cycles=2, replacement_margin=1.0))
    now = stamp()
    assert policy.decide(first, now).should_alert
    assert policy.decide(first, now + timedelta(minutes=6)).reason == "unchanged"
    assert policy.decide(replacement, now + timedelta(minutes=6)).reason == "replacement-debounce"
    assert policy.decide(replacement, now + timedelta(minutes=12)).should_alert
    changed = batch(item("600005"), item("600006"), item("600007"))
    assert changed is not None
    assert policy.decide(changed, now + timedelta(minutes=18)).reason == "replacement-debounce"
    assert policy.decide(changed, now + timedelta(minutes=24)).should_alert
    assert policy.decide(first, now + timedelta(minutes=30)).reason == "daily-limit"
    assert policy.decide(first, now + timedelta(days=1)).should_alert


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
