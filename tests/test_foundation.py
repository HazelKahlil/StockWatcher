from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from stock_watcher.config import ConfigRepository, VersionedConfig
from stock_watcher.domain import HealthState, MarketEvent, ProviderHealth
from stock_watcher.logging_config import configure_logging
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
        HealthState.WARMING,
        HealthState.WARMING,
        HealthState.WARMING,
        HealthState.HEALTHY,
    ]
    assert not first[3].is_candidate_safe
    assert first[-1].is_candidate_safe


def test_replay_deduplicates_data_and_only_recovers_after_fresh_warming_samples() -> None:
    events = (
        builder()
        .normal(10.0)
        .duplicate_timestamp()
        .stopped()
        .warming()
        .warming()
        .warming()
        .normal(11.0)
        .build()
    )
    replayed = tuple(ReplayProvider(events).events())
    assert len(replayed) == 6
    assert replayed[1].snapshot is None
    assert replayed[1].health.state is HealthState.STOPPED
    replayed_prices = [event.snapshot.price for event in replayed if event.snapshot]
    assert replayed_prices == [10.0, 10.0, 10.0, 10.0, 11.0]
    assert [event.is_candidate_safe for event in replayed] == [
        True,
        False,
        False,
        False,
        False,
        True,
    ]


def test_stopped_event_survives_duplicate_source_timestamp_with_full_provenance() -> None:
    normal = builder().normal(10.0).build()[0]
    assert normal.snapshot is not None
    normal_snapshot = normal.snapshot
    stopped = MarketEvent(
        snapshot=normal_snapshot,
        health=ProviderHealth(
            HealthState.STOPPED,
            normal_snapshot.source_ts,
            normal_snapshot.received_ts,
            normal_snapshot.provider_version,
            normal_snapshot.config_version,
            "simulated disconnect",
        ),
    )
    replayed = tuple(ReplayProvider((normal, stopped)).events())
    assert [event.health.state for event in replayed] == [HealthState.HEALTHY, HealthState.STOPPED]
    assert replayed[-1].snapshot is None
    assert replayed[-1].health.source_ts == normal_snapshot.source_ts
    assert replayed[-1].health.received_ts == normal_snapshot.received_ts
    assert replayed[-1].health.provider_version == normal_snapshot.provider_version
    assert replayed[-1].health.config_version == normal_snapshot.config_version


def test_domain_rejects_non_shanghai_timestamps() -> None:
    with pytest.raises(ValueError, match="Asia/Shanghai"):
        SyntheticScenarioBuilder(datetime(2026, 7, 22, 9, 30, tzinfo=ZoneInfo("UTC")))


def test_logging_redacts_sensitive_values_and_rolls_files(tmp_path: Path) -> None:
    logger = configure_logging(
        tmp_path / "logs", logger_name="stock_watcher.tests", max_bytes=80, backup_count=2
    )
    logger.info("token=abc123 account_id=internal-user-42")
    for handler in logger.handlers:
        handler.flush()
    initial_content = (tmp_path / "logs" / "stock-watcher.log").read_text(encoding="utf-8")
    for number in range(4):
        logger.info("health event %s %s", number, "x" * 48)
    for handler in logger.handlers:
        handler.flush()

    files = sorted((tmp_path / "logs").glob("stock-watcher.log*"))
    assert len(files) > 1
    assert "abc123" not in initial_content
    assert "internal-user-42" not in initial_content
    assert "[REDACTED]" in initial_content


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
