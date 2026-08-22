from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from stock_watcher.build_info import source_commit
from stock_watcher.config import DataSourceSettings
from stock_watcher.domain import SHANGHAI, CandidateInput, HealthState, Security
from stock_watcher.engine import CandidateBatch, CandidateConfig, CandidateEngine
from stock_watcher.providers.tushare import Tushare15000Provider
from stock_watcher.providers.tushare.capabilities import (
    ProviderCapability,
    ProviderCapabilityState,
    ProviderCapabilityStatus,
)
from stock_watcher.runtime import (
    AutomationPlanner,
    AutomationTaskState,
    AutomationTaskType,
    RuntimeUniverse,
    ScanOutcome,
    TushareV1Runtime,
)
from stock_watcher.security import PRIMARY_CREDENTIAL, CredentialStore, MemoryCredentialStore
from stock_watcher.storage import SQLiteStore
from stock_watcher.ui.tushare_v1_session import TushareV1Session


def stamp(hour: int = 9, minute: int = 45, second: int = 0) -> datetime:
    return datetime(2026, 8, 3, hour, minute, second, tzinfo=SHANGHAI)


def candidate_input(code: str, *, score: float = 5.0) -> CandidateInput:
    return CandidateInput(
        security=Security(code=code, name=code, market=code.rpartition(".")[2]),
        price=10.0,
        change_pct=score,
        velocity_pct=1.0,
        sector="测试板块",
        sector_strength=25.0,
        trend_3d_pct=1.0,
        source_ts=stamp(),
        received_ts=stamp(),
        provider_version="test",
        config_version="test",
        velocity_1m_pct=1.0,
        velocity_3m_pct=1.0,
        velocity_5m_pct=1.0,
        sector_code=f"I-{code}",
        sector_gate_passed=True,
        sector_up_ratio=0.8,
        sector_strong_count=5,
        sector_rank_percentile=0.1,
        sector_valid_count=10,
        data_completeness=1.0,
    )


def batch(at: datetime | None = None) -> CandidateBatch:
    calculated = CandidateEngine().calculate(
        tuple(candidate_input(f"60000{i}.SH", score=6 - i / 10) for i in range(1, 4)),
        HealthState.HEALTHY,
        CandidateConfig("test", "test"),
    )
    assert calculated is not None
    if at is None:
        return calculated
    return replace(calculated, source_ts=at, generated_at=at)


class _Clock:
    def __init__(self, values: list[datetime]) -> None:
        self.values = values

    def __call__(self) -> datetime:
        if len(self.values) == 1:
            return self.values[0]
        return self.values.pop(0)


class _FakeHealth:
    required_cycles = 3


class _UnknownCapabilities:
    def __init__(self) -> None:
        self.started = 0
        self.in_flight = False

    def statuses(self) -> dict[ProviderCapability, ProviderCapabilityStatus]:
        return {
            capability: ProviderCapabilityStatus(
                capability=capability,
                state=ProviderCapabilityState.UNKNOWN,
            )
            for capability in ProviderCapability
        }

    def seed_realtime_codes(self, _codes: object) -> None:
        return

    def start_realtime_background(self) -> bool:
        self.started += 1
        return True

    def start_background(self) -> bool:
        self.started += 1
        return True

    def shutdown(self) -> None:
        return


class _StaleUsableRuntime:
    def __init__(self, outcome: ScanOutcome, *, now: datetime) -> None:
        self.outcome = outcome
        self.scan_calls = 0
        self.prepare_calls = 0
        self.universe = RuntimeUniverse(
            profiles=(),
            memberships=(),
            trends={},
            high_3d={},
            open_dates=(now.date() - timedelta(days=1),),
            concept_loaded=False,
        )
        self.health = _FakeHealth()

    def universe_is_current(self, _now: datetime) -> bool:
        return False

    def universe_is_usable(self, _now: datetime) -> bool:
        return True

    def prepare(self) -> RuntimeUniverse:
        self.prepare_calls += 1
        return self.universe

    def scan_once(self) -> ScanOutcome:
        self.scan_calls += 1
        return self.outcome

    def request_scan_cancellation(self) -> None:
        return


def _session(
    tmp_path: Path,
    runtime: _StaleUsableRuntime,
    clock: _Clock,
) -> TushareV1Session:
    credentials = MemoryCredentialStore()
    credentials.set(PRIMARY_CREDENTIAL, "test-token")

    def factory(
        _settings: DataSourceSettings,
        _store: CredentialStore,
    ) -> tuple[TushareV1Runtime, Tushare15000Provider]:
        return (
            cast(TushareV1Runtime, runtime),
            cast(Tushare15000Provider, object()),
        )

    return TushareV1Session(
        tmp_path / "watcher.sqlite3",
        credential_store=credentials,
        settings=DataSourceSettings(),
        runtime_factory=factory,
        clock=clock,
    )


def test_automation_planner_creates_deterministic_daily_obligations() -> None:
    planner = AutomationPlanner()
    specs = planner.for_date(date(2026, 8, 3))
    assert [spec.task_type for spec in specs] == [
        AutomationTaskType.FIXED_0945,
        AutomationTaskType.FIXED_1445,
        AutomationTaskType.SUMMARY_1530,
    ]
    assert planner.due(stamp(9, 45)) == (specs[0],)
    assert planner.due(stamp(14, 45)) == (specs[1],)
    assert planner.due(stamp(15, 30)) == (specs[2],)


def test_fixed_0945_scans_with_degraded_static_cache_and_persists_task(
    tmp_path: Path,
) -> None:
    now = stamp(9, 45)
    current_batch = batch(now)
    runtime = _StaleUsableRuntime(
        ScanOutcome(
            HealthState.HEALTHY,
            "正常",
            current_batch,
            current_batch,
            None,
            1.0,
            1.0,
            1.0,
            1.0,
        ),
        now=now,
    )
    session = _session(tmp_path, runtime, _Clock([now, now + timedelta(seconds=5)]))
    try:
        session.recover()
        alert = session.consume_pending_alert()
        assert alert is not None
        assert alert.title == "09:45 观察提醒"
        assert runtime.scan_calls == 1
        task = session.store.get_automation_task(
            "2026-08-03:scheduled-09:45"
        )
        assert task is not None
        assert task["state"] == AutomationTaskState.SUCCEEDED.value
        assert task["snapshot_id"] is not None
        scans = session.store.list_scan_runs("2026-08-03")
        assert len(scans) == 1
        assert scans[0]["trigger_type"] == AutomationTaskType.FIXED_0945.value
    finally:
        session.shutdown()



def test_capability_probe_is_diagnostic_not_a_fixed_scan_gate(tmp_path: Path) -> None:
    now = stamp(14, 45)
    current_batch = batch(now)
    runtime = _StaleUsableRuntime(
        ScanOutcome(
            HealthState.HEALTHY,
            "正常",
            current_batch,
            current_batch,
            None,
            1.0,
            1.0,
            1.0,
            1.0,
        ),
        now=now,
    )
    session = _session(tmp_path, runtime, _Clock([now, now + timedelta(seconds=5)]))
    capabilities = _UnknownCapabilities()
    session._capability_checks_required = True
    session.capability_checks = cast(Any, capabilities)
    try:
        session.recover()
        alert = session.consume_pending_alert()
        assert alert is not None
        assert alert.title == "14:45 观察提醒"
        assert runtime.scan_calls == 1
        assert capabilities.started == 1
    finally:
        session.shutdown()


def test_manual_fetch_does_not_overwrite_due_fixed_alert(tmp_path: Path) -> None:
    now = stamp(9, 45)
    current_batch = batch(now)
    runtime = _StaleUsableRuntime(
        ScanOutcome(
            HealthState.HEALTHY,
            "正常",
            current_batch,
            current_batch,
            None,
            1.0,
            1.0,
            1.0,
            1.0,
        ),
        now=now,
    )
    session = _session(tmp_path, runtime, _Clock([now, now + timedelta(seconds=5)]))
    try:
        session.manual_fetch()
        alert = session.consume_pending_alert()
        assert alert is not None
        assert alert.title == "09:45 观察提醒"
        assert alert.trigger_type == AutomationTaskType.FIXED_0945.value
    finally:
        session.shutdown()

def test_1530_summary_falls_back_to_local_scan_records_without_provider(
    tmp_path: Path,
) -> None:
    now = stamp(15, 30)
    db = tmp_path / "summary.sqlite3"
    store = SQLiteStore(db)
    store.initialize()
    current = batch(stamp(14, 56))
    store.record_scan_run(
        {
            "started_at": stamp(14, 55).isoformat(),
            "completed_at": stamp(14, 56).isoformat(),
            "trigger_type": "automatic",
            "health": HealthState.HEALTHY.value,
            "detail": "正常",
            "stable_batch_json": current.trace_payload(),
            "audit_json": "{}",
        }
    )
    def unused_factory(
        _settings: DataSourceSettings,
        _store: CredentialStore,
    ) -> tuple[TushareV1Runtime, Tushare15000Provider]:
        return (
            cast(TushareV1Runtime, object()),
            cast(Tushare15000Provider, object()),
        )

    session = TushareV1Session(
        db,
        credential_store=MemoryCredentialStore(),
        runtime_factory=unused_factory,
        clock=_Clock([now]),
    )
    session.recover()
    summary = store.get_daily_summary("2026-08-03")
    assert summary is not None
    assert summary["version"] == "daily-summary-local-fallback-v1"
    assert summary["alert_count"] == 0
    task = store.get_automation_task("2026-08-03:summary-15:30")
    assert task is not None
    assert task["state"] == AutomationTaskState.SUCCEEDED.value


def test_history_prune_keeps_recent_unreferenced_observations(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "history.sqlite3")
    store.initialize()
    now = stamp(15, 40)
    recent_id = store.record_batch(batch(now - timedelta(days=1)))
    old_id = store.record_batch(batch(now - timedelta(days=31)))

    store.prune_history(before=now - timedelta(days=30))

    ids = {int(row["id"]) for row in store.list_recent_snapshots(limit=20)}
    assert recent_id in ids
    assert old_id not in ids


def test_selection_audit_explains_raw_and_stable_top3() -> None:
    engine = CandidateEngine()
    config = CandidateConfig("test", "test")
    inputs = tuple(
        candidate_input(f"60000{i}.SH", score=7 - i / 10)
        for i in range(1, 6)
    )
    raw = engine.calculate(inputs, HealthState.HEALTHY, config)
    assert raw is not None
    stable = replace(
        raw,
        candidates=(raw.candidates[1], raw.candidates[0], raw.candidates[2]),
    )
    audit = engine.build_selection_audit(inputs, raw, stable, config)
    assert audit.warmup_state == "ready"
    assert audit.display_velocity_ready
    assert audit.raw_codes != audit.stable_codes
    payload = json.loads(audit.trace_payload())
    assert payload["rows"][0]["decision"] in {"displayed", "retained_by_stability"}


def test_source_commit_can_be_verified_from_packaging_environment() -> None:
    # Use os.environ directly so this remains a pure build-provenance test and
    # does not depend on a Git checkout at runtime.
    original = os.environ.get("STOCKWATCHER_SOURCE_COMMIT")
    os.environ["STOCKWATCHER_SOURCE_COMMIT"] = "abc123"
    try:
        assert source_commit() == "abc123"
    finally:
        if original is None:
            os.environ.pop("STOCKWATCHER_SOURCE_COMMIT", None)
        else:
            os.environ["STOCKWATCHER_SOURCE_COMMIT"] = original
