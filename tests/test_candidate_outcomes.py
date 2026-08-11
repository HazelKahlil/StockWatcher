from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from PySide6.QtCore import QThread

from stock_watcher.domain import (
    SHANGHAI,
    CandidateOutcome,
    DataQuality,
    HealthState,
    OutcomeResult,
    OutcomeSlot,
    OutcomeStatus,
    RealtimeQuote,
    Security,
    SettlementMethod,
    build_outcome_review,
)
from stock_watcher.engine import AlertTrigger, Candidate, CandidateBatch
from stock_watcher.providers.tushare.errors import (
    ProviderError,
    ProviderFailureReason,
)
from stock_watcher.providers.tushare.models import (
    DataQuality as TransportQuality,
)
from stock_watcher.providers.tushare.models import (
    ProviderProvenance,
    SourceTimestampKind,
    TransportResult,
)
from stock_watcher.runtime import CandidateOutcomeTracker, OutcomeActionReport, ScanOutcome
from stock_watcher.storage import SQLiteStore
from stock_watcher.ui.outcome_review import (
    OutcomeReviewWorker,
    _method,
    _prices,
    _result,
)
from stock_watcher.ui.tushare_v1_session import TushareV1Session


def stamp(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SHANGHAI)


def candidate_batch(
    at: datetime,
    *,
    codes: tuple[str, str, str] = ("000001.SZ", "600000.SH", "300001.SZ"),
    prices: tuple[float, float, float] = (10.0, 20.0, 30.0),
    provider_version: str = "tushare-test-v1",
    formal: bool = True,
) -> CandidateBatch:
    candidates = tuple(
        Candidate(
            code=code,
            name=f"股票{rank}",
            sector=f"行业{rank}",
            level="强",
            score=50.0,
            price_score=20.0,
            sector_score=20.0,
            trend_score=10.0,
            penalty=0.0,
            reasons=("测试候选",),
            source_ts=at,
            provider_version=provider_version,
            config_version="candidate-outcome-test-v1",
            app_version="0.6.0a1",
            price=price,
            is_formal=formal,
            is_supplement=not formal,
        )
        for rank, (code, price) in enumerate(zip(codes, prices), start=1)
    )
    return CandidateBatch(
        source_ts=at,
        generated_at=at + timedelta(seconds=1),
        candidates=candidates,
        health=HealthState.HEALTHY,
        overall_weak=False,
        formal_count=3 if formal else 0,
    )


def transport_result(
    records: tuple[dict[str, str | int | float | bool | None], ...],
    *,
    at: datetime,
    quality: TransportQuality = TransportQuality.HEALTHY,
) -> TransportResult:
    return TransportResult(
        records=records,
        http_status=200,
        elapsed_seconds=0.01,
        provenance=ProviderProvenance(
            provider_profile="test",
            endpoint="test",
            provider_version="test-v1",
            schema_version="test-v1",
            source_ts=at,
            received_ts=at,
            source_timestamp_kind=SourceTimestampKind.SUPPLIER,
            freshness_seconds=0.0,
            quality=quality,
            degraded=quality is not TransportQuality.HEALTHY,
            fields_used=(),
        ),
    )


class FakeOutcomeProvider:
    def __init__(self, open_dates: tuple[date, ...]) -> None:
        self.open_dates = open_dates
        self.realtime_records: tuple[
            dict[str, str | int | float | bool | None], ...
        ] = ()
        self.historical_records: dict[
            tuple[str, str],
            tuple[dict[str, str | int | float | bool | None], ...],
        ] = {}
        self.realtime_error: Exception | None = None
        self.historical_error: Exception | None = None
        self.calendar_calls = 0
        self.realtime_calls: list[tuple[str, ...]] = []
        self.historical_calls: list[tuple[str, str]] = []

    def trading_dates(self, **params: str | int | float | bool) -> TransportResult:
        self.calendar_calls += 1
        end = max(self.open_dates)
        records: tuple[dict[str, str | int | float | bool | None], ...] = tuple(
            {"cal_date": value.strftime("%Y%m%d"), "is_open": 1}
            for value in self.open_dates
        )
        return transport_result(records, at=stamp(end, 15, 0))

    def realtime_quotes(self, codes: tuple[str, ...]) -> TransportResult:
        self.realtime_calls.append(codes)
        if self.realtime_error is not None:
            raise self.realtime_error
        at = (
            datetime.fromisoformat(str(self.realtime_records[0]["source_ts"]))
            if self.realtime_records
            else stamp(max(self.open_dates), 9, 45)
        )
        return transport_result(self.realtime_records, at=at)

    def historical_minutes(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult:
        code = str(params["ts_code"])
        target = str(params["start_date"])
        self.historical_calls.append((code, target))
        if self.historical_error is not None:
            raise self.historical_error
        records = self.historical_records.get((code, target), ())
        return transport_result(records, at=datetime.fromisoformat(target).replace(tzinfo=SHANGHAI))


def quote(
    code: str,
    at: datetime,
    price: float,
    *,
    quality: DataQuality = DataQuality.GOOD,
    volume: float = 100.0,
    state: str = "trading",
) -> RealtimeQuote:
    return RealtimeQuote(
        security=Security(code=code, name=code, market=code.rpartition(".")[2]),
        price=price,
        previous_close=max(0.01, price - 0.1),
        open=price,
        high=price,
        low=price,
        volume_shares=volume,
        amount_cny=volume * price,
        source_ts=at,
        received_ts=at,
        scan_id="outcome-test-scan",
        provider_version="tushare-test-v1",
        quality=quality,
        trading_state=state,
    )


def test_scheduled_slots_use_verified_calendar_and_are_idempotent(tmp_path: Path) -> None:
    friday = date(2026, 8, 7)
    tuesday = date(2026, 8, 11)  # Monday is deliberately a calendar holiday fixture.
    provider = FakeOutcomeProvider((tuesday,))
    store = SQLiteStore(tmp_path / "outcomes.sqlite3")
    store.initialize()
    tracker = CandidateOutcomeTracker(store, provider)
    morning = candidate_batch(stamp(friday, 9, 45))

    first = tracker.record_scheduled_batch(
        morning,
        snapshot_id=10,
        alert_id=20,
        trigger_type="scheduled-09:45",
        recorded_at=stamp(friday, 9, 45),
    )
    duplicate = tracker.record_scheduled_batch(
        morning,
        snapshot_id=10,
        alert_id=20,
        trigger_type="scheduled-09:45",
        recorded_at=stamp(friday, 9, 45),
    )
    afternoon = candidate_batch(
        stamp(friday, 14, 45),
        codes=("000001.SZ", "600001.SH", "300002.SZ"),
    )
    tracker.record_scheduled_batch(
        afternoon,
        snapshot_id=11,
        alert_id=21,
        trigger_type="scheduled-14:45",
        recorded_at=stamp(friday, 14, 45),
    )

    rows = store.list_candidate_outcomes(trading_days=None)
    assert first.created == 3
    assert duplicate.created == 0
    assert len(rows) == 6
    assert {row["target_trade_date"] for row in rows} == {tuesday.isoformat()}
    assert {row["slot"] for row in rows} == {"09:45", "14:45"}
    assert sum(row["code"] == "000001.SZ" for row in rows) == 2
    assert provider.calendar_calls == 3


def test_supplement_candidates_never_enter_live_or_historical_outcomes(
    tmp_path: Path,
) -> None:
    entry_date = date(2026, 8, 10)
    target_date = date(2026, 8, 11)
    provider = FakeOutcomeProvider((target_date,))
    store = SQLiteStore(tmp_path / "supplements.sqlite3")
    store.initialize()
    batch = candidate_batch(stamp(entry_date, 9, 45), formal=False)
    snapshot_id = store.record_batch(batch)
    alert_id = store.record_alert_event(
        snapshot_id,
        stamp(entry_date, 9, 45).isoformat(),
        "scheduled-09:45",
        "macos-desktop",
        "scheduled-09:45",
    )
    tracker = CandidateOutcomeTracker(store, provider)

    direct = tracker.record_scheduled_batch(
        batch,
        snapshot_id=snapshot_id,
        alert_id=alert_id,
        trigger_type="scheduled-09:45",
        recorded_at=stamp(entry_date, 9, 45),
    )
    historical = tracker.backfill_recent_scheduled(
        now=stamp(target_date, 16, 0),
        days=30,
    )

    assert direct.created == 0
    assert direct.skipped == 3
    assert historical.created == 0
    assert historical.skipped == 3
    assert historical.safe_reasons == ("scheduled_candidate_not_formal",)
    assert store.list_candidate_outcomes(trading_days=None) == []


def test_new_snapshot_calendar_resolution_is_not_starved_by_older_pending_rows(
    tmp_path: Path,
) -> None:
    entry_date = date(2026, 8, 10)
    target_date = date(2026, 8, 11)
    provider = FakeOutcomeProvider((target_date,))
    store = SQLiteStore(tmp_path / "target-scope.sqlite3")
    store.initialize()
    tracker = CandidateOutcomeTracker(store, provider)
    tracker.record_scheduled_batch(
        candidate_batch(stamp(entry_date, 9, 45)),
        snapshot_id=100,
        alert_id=100,
        trigger_type="scheduled-09:45",
        recorded_at=stamp(entry_date, 9, 45),
        resolve_calendar=False,
    )
    tracker.record_scheduled_batch(
        candidate_batch(stamp(entry_date, 14, 45)),
        snapshot_id=101,
        alert_id=101,
        trigger_type="scheduled-14:45",
        recorded_at=stamp(entry_date, 14, 45),
        resolve_calendar=False,
    )

    tracker.resolve_pending_targets(
        now=stamp(entry_date, 14, 45),
        limit=3,
        entry_snapshot_id=101,
    )
    rows = store.list_candidate_outcomes(trading_days=None)
    older = [row for row in rows if row["entry_snapshot_id"] == 100]
    current = [row for row in rows if row["entry_snapshot_id"] == 101]

    assert {row["target_trade_date"] for row in older} == {None}
    assert {row["target_trade_date"] for row in current} == {target_date.isoformat()}


def test_scan_reuse_settles_old_rows_before_same_stock_reentry(tmp_path: Path) -> None:
    friday = date(2026, 8, 7)
    monday = date(2026, 8, 10)
    tuesday = date(2026, 8, 11)
    provider = FakeOutcomeProvider((monday, tuesday))
    store = SQLiteStore(tmp_path / "settlement.sqlite3")
    store.initialize()
    tracker = CandidateOutcomeTracker(store, provider)
    entry = candidate_batch(stamp(friday, 9, 45))
    tracker.record_scheduled_batch(
        entry,
        snapshot_id=1,
        alert_id=1,
        trigger_type="scheduled-09:45",
        recorded_at=stamp(friday, 9, 45),
    )
    prices = (10.5, 19.0, 30.0)
    report = tracker.settle_fixed_slot(
        target_trade_date=monday,
        slot=OutcomeSlot.MORNING,
        scan_quotes=tuple(
            quote(code, stamp(monday, 9, 45), price)
            for code, price in zip((item.code for item in entry.candidates), prices)
        ),
        now=stamp(monday, 9, 45) + timedelta(seconds=10),
    )
    tracker.record_scheduled_batch(
        candidate_batch(stamp(monday, 9, 45), prices=prices),
        snapshot_id=2,
        alert_id=2,
        trigger_type="scheduled-09:45",
        recorded_at=stamp(monday, 9, 45),
    )

    rows = store.list_candidate_outcomes(trading_days=None)
    assert report.settled == 3
    assert provider.realtime_calls == []
    assert len(rows) == 6
    assert [row["status"] for row in rows].count("settled") == 3
    assert [row["status"] for row in rows].count("pending") == 3
    first = next(row for row in rows if row["entry_snapshot_id"] == 1 and row["rank"] == 1)
    assert first["outcome"] == "win"
    assert first["settlement_method"] == "realtime_scan"


def test_realtime_fallback_is_one_batch_and_minute_backfill_is_serial(tmp_path: Path) -> None:
    entry_date = date(2026, 8, 10)
    target_date = date(2026, 8, 11)
    provider = FakeOutcomeProvider((target_date,))
    store = SQLiteStore(tmp_path / "fallback.sqlite3")
    store.initialize()
    tracker = CandidateOutcomeTracker(store, provider)
    batch = candidate_batch(stamp(entry_date, 14, 45))
    tracker.record_scheduled_batch(
        batch,
        snapshot_id=3,
        alert_id=3,
        trigger_type="scheduled-14:45",
        recorded_at=stamp(entry_date, 14, 45),
    )
    provider.realtime_error = ProviderError(ProviderFailureReason.NETWORK)
    for candidate, close in zip(batch.candidates, (10.2, 19.5, 30.0)):
        target = f"{target_date.isoformat()} 14:45:00"
        provider.historical_records[(candidate.code, target)] = (
            {
                "ts_code": candidate.code,
                "trade_time": target,
                "close": close,
                "vol": 100,
                "amount": close * 100,
            },
        )

    realtime = tracker.settle_fixed_slot(
        target_trade_date=target_date,
        slot=OutcomeSlot.AFTERNOON,
        scan_quotes=(),
        now=stamp(target_date, 14, 45) + timedelta(seconds=10),
    )
    historical = tracker.backfill_due(
        now=stamp(target_date, 14, 46),
        limit=3,
    )

    assert realtime.pending == 3
    assert provider.realtime_calls == [tuple(candidate.code for candidate in batch.candidates)]
    assert historical.settled == 3
    assert len(provider.historical_calls) == 3
    rows = store.list_candidate_outcomes(trading_days=None)
    assert {row["settlement_method"] for row in rows} == {"historical_minute"}


def test_invalid_realtime_and_missing_minutes_never_count_as_losses(tmp_path: Path) -> None:
    entry_date = date(2026, 8, 10)
    target_date = date(2026, 8, 11)
    provider = FakeOutcomeProvider((target_date,))
    store = SQLiteStore(tmp_path / "invalid.sqlite3")
    store.initialize()
    tracker = CandidateOutcomeTracker(store, provider, max_realtime_age_seconds=60)
    batch = candidate_batch(stamp(entry_date, 9, 45))
    tracker.record_scheduled_batch(
        batch,
        snapshot_id=4,
        alert_id=4,
        trigger_type="scheduled-09:45",
        recorded_at=stamp(entry_date, 9, 45),
    )
    now = stamp(target_date, 9, 45) + timedelta(seconds=30)
    provider.realtime_records = (
        {
            "ts_code": batch.candidates[0].code,
            "price": 0,
            "source_ts": stamp(target_date, 9, 45).isoformat(),
            "data_quality": "HEALTHY",
            "vol": 0,
            "amount": 0,
        },
        {
            "ts_code": batch.candidates[1].code,
            "price": 20,
            "source_ts": stamp(entry_date, 9, 45).isoformat(),
            "data_quality": "HEALTHY",
            "vol": 100,
            "amount": 2000,
        },
        {
            "ts_code": batch.candidates[2].code,
            "price": 30,
            "source_ts": (stamp(target_date, 9, 45) - timedelta(minutes=3)).isoformat(),
            "data_quality": "HEALTHY",
            "vol": 100,
            "amount": 3000,
        },
    )

    report = tracker.settle_fixed_slot(
        target_trade_date=target_date,
        slot=OutcomeSlot.MORNING,
        scan_quotes=(),
        now=now,
    )
    missing = tracker.backfill_due(now=stamp(target_date, 9, 46), limit=3)
    rows = store.list_candidate_outcomes(trading_days=None)

    assert report.settled == 0
    assert missing.unavailable == 3
    assert {row["status"] for row in rows} == {"unavailable"}
    assert {row["outcome"] for row in rows} == {None}
    review = build_outcome_review(
        tuple(CandidateOutcome.from_mapping(row) for row in rows)
    )
    assert review.overall.win_rate is None


def test_historical_empty_data_marks_outcomes_unavailable(tmp_path: Path) -> None:
    entry_date = date(2026, 8, 10)
    target_date = date(2026, 8, 11)
    provider = FakeOutcomeProvider((target_date,))
    provider.historical_error = ProviderError(ProviderFailureReason.EMPTY_DATA)
    store = SQLiteStore(tmp_path / "empty-minutes.sqlite3")
    store.initialize()
    tracker = CandidateOutcomeTracker(store, provider)
    tracker.record_scheduled_batch(
        candidate_batch(stamp(entry_date, 14, 45)),
        snapshot_id=5,
        alert_id=5,
        trigger_type="scheduled-14:45",
        recorded_at=stamp(entry_date, 14, 45),
    )

    report = tracker.backfill_due(now=stamp(target_date, 14, 46), limit=3)
    rows = store.list_candidate_outcomes(trading_days=None)

    assert report.unavailable == 3
    assert report.pending == 0
    assert len(provider.historical_calls) == 3
    assert {row["status"] for row in rows} == {"unavailable"}
    assert {row["outcome"] for row in rows} == {None}
    assert {row["safe_reason"] for row in rows} == {
        "historical_minute_missing_or_ambiguous"
    }


def test_statistics_exclude_pending_and_require_all_six_for_portfolio() -> None:
    first = date(2026, 8, 7)
    second = date(2026, 8, 10)
    records: list[CandidateOutcome] = []
    returns = (1.0, -1.0, 0.0, 2.0, -2.0, 3.0)
    for day_index, day in enumerate((first, second)):
        for index in range(6):
            slot = OutcomeSlot.MORNING if index < 3 else OutcomeSlot.AFTERNOON
            settled = not (day_index == 1 and index == 5)
            value = returns[index] if settled else None
            outcome = (
                OutcomeResult.WIN
                if value is not None and value > 0
                else OutcomeResult.LOSS
                if value is not None and value < 0
                else OutcomeResult.FLAT
                if value == 0
                else None
            )
            records.append(
                outcome_record(
                    record_id=day_index * 6 + index + 1,
                    entry_date=day,
                    slot=slot,
                    rank=index % 3 + 1,
                    return_value=value,
                    outcome=outcome,
                    status=(OutcomeStatus.SETTLED if settled else OutcomeStatus.PENDING),
                )
            )
    review = build_outcome_review(tuple(records))

    assert review.overall.total_count == 12
    assert review.overall.settled_count == 11
    assert review.overall.win_count == 5
    assert review.overall.flat_count == 2
    assert review.overall.win_rate == pytest.approx(5 / 11)
    assert review.morning.settled_count == 6
    assert review.afternoon.settled_count == 5
    assert review.complete_portfolio_days == 1
    assert review.portfolio_win_rate == 1.0
    assert review.portfolios[0].complete is False
    assert review.portfolios[1].average_return_pct == pytest.approx(0.5)


def test_historical_backfill_accepts_only_real_scheduled_three(tmp_path: Path) -> None:
    entry_date = date(2026, 8, 7)
    target_date = date(2026, 8, 10)
    now = stamp(target_date, 16, 0)
    provider = FakeOutcomeProvider((target_date,))
    store = SQLiteStore(tmp_path / "history.sqlite3")
    store.initialize()
    valid = candidate_batch(stamp(entry_date, 9, 45))
    valid_snapshot = store.record_batch(valid)
    store.record_alert_event(
        valid_snapshot,
        stamp(entry_date, 9, 45).isoformat(),
        "scheduled-09:45",
        "macos-desktop",
        "scheduled-09:45",
    )
    store.record_alert_event(
        valid_snapshot,
        stamp(entry_date, 10, 0).isoformat(),
        "manual",
        "macos-desktop",
        "manual",
    )
    store.record_alert_event(
        valid_snapshot,
        stamp(entry_date, 10, 1).isoformat(),
        "intraday",
        "macos-desktop",
        "intraday",
    )
    replay = candidate_batch(
        stamp(entry_date, 14, 45),
        provider_version="replay-fixture-v1",
    )
    replay_snapshot = store.record_batch(replay)
    store.record_alert_event(
        replay_snapshot,
        stamp(entry_date, 14, 45).isoformat(),
        "scheduled-14:45",
        "macos-desktop",
        "scheduled-14:45",
    )
    for candidate in valid.candidates:
        target = f"{target_date.isoformat()} 09:45:00"
        provider.historical_records[(candidate.code, target)] = (
            {
                "ts_code": candidate.code,
                "trade_time": target,
                "close": candidate.price + 0.1,
                "vol": 100,
                "amount": 1000,
            },
        )

    report = CandidateOutcomeTracker(store, provider).backfill_recent_scheduled(
        now=now,
        days=30,
    )
    rows = store.list_candidate_outcomes(trading_days=None)

    assert report.created == 3
    assert report.settled == 3
    assert report.skipped == 3
    assert len(rows) == 3
    assert {row["provider_version"] for row in rows} == {"tushare-test-v1"}


def test_alert_retention_does_not_delete_candidate_outcomes(tmp_path: Path) -> None:
    entry_date = date(2026, 6, 1)
    target_date = date(2026, 6, 2)
    provider = FakeOutcomeProvider((target_date,))
    store = SQLiteStore(tmp_path / "retention.sqlite3")
    store.initialize()
    batch = candidate_batch(stamp(entry_date, 9, 45))
    snapshot_id = store.record_batch(batch)
    alert_id = store.record_alert_event(
        snapshot_id,
        stamp(entry_date, 9, 45).isoformat(),
        "scheduled-09:45",
        "macos-desktop",
        "scheduled-09:45",
    )
    CandidateOutcomeTracker(store, provider).record_scheduled_batch(
        batch,
        snapshot_id=snapshot_id,
        alert_id=alert_id,
        trigger_type="scheduled-09:45",
        recorded_at=stamp(entry_date, 9, 45),
    )

    store.prune_history(before=stamp(date(2026, 8, 11), 0, 0) - timedelta(days=31))

    assert store.list_alert_history(now=stamp(date(2026, 8, 11), 12, 0), days=90) == []
    assert len(store.list_candidate_outcomes(trading_days=None)) == 3


def test_v7_migration_failure_rolls_back_and_degrades_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "migration.sqlite3"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_version VALUES (6, '2026-08-11T09:00:00+08:00')")
        SQLiteStore._apply_v1_schema(connection)
        SQLiteStore._apply_v2_migration(connection)
        SQLiteStore._apply_v3_migration(connection)
        SQLiteStore._apply_v4_migration(connection)
        SQLiteStore._apply_v5_migration(connection)
        SQLiteStore._apply_v6_migration(connection)

    def fail_v7(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE partial_outcome_table (id INTEGER PRIMARY KEY)")
        raise RuntimeError("injected v7 failure")

    monkeypatch.setattr(SQLiteStore, "_apply_v7_migration", staticmethod(fail_v7))
    store = SQLiteStore(path)
    with pytest.raises(RuntimeError, match="injected v7 failure"):
        store.initialize()
    assert store.read_only
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (6,)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'partial_outcome_table'"
        ).fetchone() is None
    assert path.with_suffix(".sqlite3.pre-v7.bak").exists()


def test_outcome_failure_cannot_undo_durable_fixed_alert(tmp_path: Path) -> None:
    at = stamp(date(2026, 8, 11), 9, 45)
    session = TushareV1Session(tmp_path / "sidecar.sqlite3", clock=lambda: at)
    session.batch = candidate_batch(at)
    session.state = HealthState.HEALTHY

    class RaisingTracker:
        def record_scheduled_batch(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("sidecar unavailable")

    session._outcome_tracker = cast(Any, RaisingTracker())
    snapshot_id = session._record_alert(
        at,
        AlertTrigger.SCHEDULED_0945,
        "scheduled-09:45",
        "09:45 观察提醒",
        "当前最新3只",
    )

    assert snapshot_id > 0
    assert session.pending_alert is not None
    assert len(session.store.list_alert_history(now=at, days=1)) == 1
    assert session._outcome_issue == "outcome-create:RuntimeError"
    session.shutdown(exit_reason="test")


def test_session_fixed_alert_immediately_creates_three_pending_rows(tmp_path: Path) -> None:
    entry_date = date(2026, 8, 10)
    target_date = date(2026, 8, 11)
    at = stamp(entry_date, 14, 45)
    provider = FakeOutcomeProvider((target_date,))
    session = TushareV1Session(tmp_path / "session-outcomes.sqlite3", clock=lambda: at)
    session.batch = candidate_batch(at)
    session.state = HealthState.HEALTHY
    session._outcome_tracker = CandidateOutcomeTracker(session.store, provider)

    session._record_alert(
        at,
        AlertTrigger.SCHEDULED_1445,
        "scheduled-14:45",
        "14:45 观察提醒",
        "当前最新3只",
    )
    immediate = session.store.list_candidate_outcomes(trading_days=None)
    with session._outcome_future_lock:
        futures = tuple(session._outcome_futures)
    for future in futures:
        future.result(timeout=2)
    resolved = session.store.list_candidate_outcomes(trading_days=None)

    assert len(immediate) == 3
    assert {row["status"] for row in immediate} == {"pending"}
    assert {row["target_trade_date"] for row in immediate} <= {
        None,
        target_date.isoformat(),
    }
    assert {row["target_trade_date"] for row in resolved} == {target_date.isoformat()}
    session.shutdown(exit_reason="test")


def test_pending_realtime_settlement_waits_for_closed_minute_before_backfill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trade_date = date(2026, 8, 11)
    at = stamp(trade_date, 9, 45) + timedelta(seconds=20)
    session = TushareV1Session(tmp_path / "delayed-backfill.sqlite3", clock=lambda: at)
    submitted: list[Any] = []
    backfill_attempts: list[datetime] = []

    class PendingTracker:
        settle_attempts = 0

        def resolve_pending_targets(self, **_kwargs: Any) -> OutcomeActionReport:
            return OutcomeActionReport()

        def settle_fixed_slot(self, **_kwargs: Any) -> OutcomeActionReport:
            self.settle_attempts += 1
            return OutcomeActionReport(pending=3)

        def backfill_due(self, *, now: datetime, limit: int) -> OutcomeActionReport:
            backfill_attempts.append(now)
            assert limit == 3
            return OutcomeActionReport(settled=3)

    def capture(task: Any) -> bool:
        submitted.append(task)
        return True

    tracker = PendingTracker()
    session._outcome_tracker = cast(Any, tracker)
    monkeypatch.setattr(session, "_submit_outcome_task", capture)
    scan = ScanOutcome(
        health=HealthState.HEALTHY,
        detail="test",
        batch=None,
        raw_batch=None,
        strong_event=None,
        elapsed_seconds=0.1,
        coverage_ratio=1.0,
    )

    session._settle_candidate_outcomes_safely(
        AlertTrigger.SCHEDULED_0945,
        scan,
        at,
    )
    assert tracker.settle_attempts == 0
    assert len(submitted) == 1
    submitted[0]()
    assert tracker.settle_attempts == 1
    session._submit_due_outcome_fallbacks(stamp(trade_date, 9, 45,))
    assert len(submitted) == 1

    due = stamp(trade_date, 9, 45) + timedelta(minutes=1)
    session._submit_due_outcome_fallbacks(due)
    assert len(submitted) == 2
    submitted[1]()
    assert backfill_attempts == [due]
    session.shutdown(exit_reason="test")


def test_failed_fixed_fallback_alert_is_excluded_from_outcomes(tmp_path: Path) -> None:
    entry_date = date(2026, 8, 10)
    target_date = date(2026, 8, 11)
    at = stamp(entry_date, 14, 45)
    provider = FakeOutcomeProvider((target_date,))
    session = TushareV1Session(tmp_path / "failed-fixed.sqlite3", clock=lambda: at)
    session.batch = candidate_batch(at - timedelta(minutes=10))
    session.state = HealthState.STOPPED
    session._outcome_tracker = CandidateOutcomeTracker(session.store, provider)

    session._record_alert(
        at,
        AlertTrigger.SCHEDULED_1445,
        "scheduled-14:45",
        "14:45 观察提醒",
        "数据延迟，展示上次结果",
    )

    assert len(session.store.list_alert_history(now=at, days=1)) == 1
    assert session.store.list_candidate_outcomes(trading_days=None) == []
    session.shutdown(exit_reason="test")


def test_review_copy_covers_pending_unavailable_and_settled_states() -> None:
    pending = outcome_record(
        record_id=1,
        entry_date=date(2026, 8, 10),
        slot=OutcomeSlot.MORNING,
        rank=1,
        return_value=None,
        outcome=None,
        status=OutcomeStatus.PENDING,
    )
    unavailable = replace(
        pending,
        id=2,
        status=OutcomeStatus.UNAVAILABLE,
        safe_reason="historical_minute_missing_or_ambiguous",
    )
    settled = replace(
        pending,
        id=3,
        status=OutcomeStatus.SETTLED,
        exit_price=10.5,
        exit_source_ts=stamp(date(2026, 8, 11), 9, 45),
        return_pct=5.0,
        outcome=OutcomeResult.WIN,
        settlement_method=SettlementMethod.REALTIME_SCAN,
    )

    assert "待结算" in _prices(pending)
    assert "目标" in _result(pending)
    assert "无有效行情" in _prices(unavailable)
    assert _result(unavailable) == "不计入胜率"
    assert _result(settled) == "+5.00% · 赢"
    assert _method(settled) == "同次全市场扫描结算"
    assert issubclass(OutcomeReviewWorker, QThread)


def outcome_record(
    *,
    record_id: int,
    entry_date: date,
    slot: OutcomeSlot,
    rank: int,
    return_value: float | None,
    outcome: OutcomeResult | None,
    status: OutcomeStatus,
) -> CandidateOutcome:
    entry_ts = stamp(
        entry_date,
        9 if slot is OutcomeSlot.MORNING else 14,
        45,
    )
    target_date = entry_date + timedelta(days=1)
    exit_price = 10.0 * (1 + return_value / 100) if return_value is not None else None
    return CandidateOutcome(
        id=record_id,
        entry_snapshot_id=record_id,
        entry_alert_id=record_id,
        entry_trade_date=entry_date,
        slot=slot,
        rank=rank,
        code=f"{record_id:06d}.SZ",
        name=f"股票{record_id}",
        entry_price=10.0,
        entry_source_ts=entry_ts,
        target_trade_date=target_date,
        target_slot=slot,
        exit_price=exit_price,
        exit_source_ts=(
            stamp(
                target_date,
                9 if slot is OutcomeSlot.MORNING else 14,
                45,
            )
            if exit_price is not None
            else None
        ),
        return_pct=return_value,
        status=status,
        outcome=outcome,
        settlement_method=(
            SettlementMethod.REALTIME_SCAN if status is OutcomeStatus.SETTLED else None
        ),
        quality="GOOD" if status is OutcomeStatus.SETTLED else "UNVERIFIED",
        provider_version="tushare-test-v1",
        config_version="test-v1",
        app_version="0.6.0a1",
        created_at=entry_ts,
        updated_at=entry_ts,
    )
