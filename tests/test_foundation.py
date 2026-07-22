from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from stock_watcher.config import ConfigRepository, VersionedConfig
from stock_watcher.domain import HealthState
from stock_watcher.providers import MockProvider, ReplayProvider, SyntheticScenarioBuilder
from stock_watcher.storage import SQLiteStore


def builder() -> SyntheticScenarioBuilder:
    return SyntheticScenarioBuilder(datetime(2026, 7, 22, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")))


def test_synthetic_is_deterministic_and_covers_health_states() -> None:
    first = builder().normal().stale().warming().stopped().reconnect().build()
    second = builder().normal().stale().warming().stopped().reconnect().build()
    assert first == second
    assert [event.health.state for event in first] == [
        HealthState.HEALTHY,
        HealthState.STALE,
        HealthState.WARMING,
        HealthState.STOPPED,
        HealthState.STOPPED,
        HealthState.HEALTHY,
    ]
    assert not first[3].is_candidate_safe
    assert first[-1].is_candidate_safe


def test_replay_deduplicates_timestamp_and_stopped_has_no_snapshot() -> None:
    events = builder().normal(10.0).duplicate_timestamp().stopped().normal(11.0).build()
    replayed = tuple(ReplayProvider(events).events())
    assert len(replayed) == 3
    assert replayed[1].snapshot is None
    assert replayed[1].health.state is HealthState.STOPPED
    assert [event.snapshot.price for event in replayed if event.snapshot] == [10.0, 11.0]


def test_mock_provider_preserves_inputs() -> None:
    events = builder().normal().build()
    assert tuple(MockProvider(events).events()) == events


def test_config_is_versioned_and_immutable(tmp_path: Path) -> None:
    repository = ConfigRepository(tmp_path / "config")
    config = VersionedConfig(version="v0.1", source="test", settings={"seed": 7})
    repository.save(config)
    assert repository.load("v0.1") == config
    with pytest.raises(FileExistsError):
        repository.save(config)


def test_sqlite_wal_backup_and_rollback(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "watcher.sqlite3")
    store.initialize()
    store.put_note("mode", "simulated")
    with store.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    backup = store.backup(tmp_path / "watcher.backup.sqlite3")
    store.put_note("mode", "changed")
    store.rollback(backup)
    assert store.get_note("mode") == "simulated"
