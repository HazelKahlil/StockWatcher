from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from stock_watcher.config import DataSourceMode, DataSourceSettings
from stock_watcher.domain import (
    CandidateInput,
    DataQuality,
    HealthState,
    RealtimeQuote,
    RollingFeatures,
    SectorMembership,
    Security,
    SourceTimestampKind,
)
from stock_watcher.engine import (
    AlertPolicy,
    AlertTrigger,
    CandidateConfig,
    CandidateEngine,
    DailySummaryEngine,
    FundCapability,
    FundEngine,
    MarketSnapshotBuffer,
    SectorEngine,
    SnapshotSequenceError,
    StableTop3Selector,
    StrongMovementDetector,
    build_post_close_review,
)
from stock_watcher.providers.tushare import Tushare15000Provider
from stock_watcher.providers.tushare.capabilities import (
    CAPABILITY_ORDER,
    ProviderCapability,
    ProviderCapabilityState,
    ProviderCapabilityStatus,
)
from stock_watcher.providers.tushare.models import (
    DataQuality as ProviderDataQuality,
)
from stock_watcher.providers.tushare.models import (
    ProviderProvenance,
    TransportResult,
)
from stock_watcher.providers.tushare.models import (
    SourceTimestampKind as ProviderTimestampKind,
)
from stock_watcher.runtime import (
    DataHealthTracker,
    FullMarketScanCoordinator,
    IncompleteScanError,
    MarketScan,
    MarketSessionSchedule,
    RuntimeUniverse,
    ScanOutcome,
    TushareV1Runtime,
)
from stock_watcher.security import (
    FAST_CREDENTIAL,
    PRIMARY_CREDENTIAL,
    MemoryCredentialStore,
)
from stock_watcher.storage import SQLiteStore
from stock_watcher.ui.data_source_settings import DataSourceSettingsController
from stock_watcher.ui.data_source_status import CredentialTestResult
from stock_watcher.ui.tushare_v1_session import (
    TushareV1Session,
    _required_capabilities_ready,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def timestamp(minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 7, 30, 10, minute, second, tzinfo=SHANGHAI)


def security(code: str) -> Security:
    suffix = code.rpartition(".")[2]
    return Security(code, f"样本{code[:6]}", suffix)


def quote(
    code: str,
    at: datetime,
    *,
    price: float,
    volume: float,
    amount: float,
    scan_id: str = "scan",
) -> RealtimeQuote:
    return RealtimeQuote(
        security=security(code),
        price=price,
        previous_close=10.0,
        open=10.0,
        high=price,
        low=9.9,
        volume_shares=volume,
        amount_cny=amount,
        source_ts=at,
        received_ts=at + timedelta(seconds=1),
        scan_id=scan_id,
        provider_version="native-test",
    )


def test_snapshot_buffer_calculates_real_1_3_5_minute_and_amount_ratio() -> None:
    buffer = MarketSnapshotBuffer()
    code = "600001.SH"
    priming = tuple(
        quote(
            code,
            timestamp() - timedelta(minutes=6 - index),
            price=10.0 + index * 0.1,
            volume=1000.0 + index * 100,
            amount=10000.0 + index * 1000,
            scan_id=f"prime-{index}",
        )
        for index in range(6)
    )
    buffer.prime(priming)
    current = quote(
        code,
        timestamp(),
        price=10.6,
        volume=1800.0,
        amount=18000.0,
        scan_id="current",
    )

    feature = buffer.update((current,), high_3d={code: 10.55})[0]

    assert feature.velocity_1m_pct == pytest.approx((10.6 / 10.5 - 1) * 100)
    assert feature.velocity_3m_pct == pytest.approx((10.6 / 10.3 - 1) * 100)
    assert feature.velocity_5m_pct == pytest.approx((10.6 / 10.1 - 1) * 100)
    assert feature.amount_delta_1m == 3000
    assert feature.amount_ratio_1m == pytest.approx(3.0)
    assert feature.intraday_high_break
    assert feature.high_3d_break


def test_snapshot_buffer_rejects_time_or_cumulative_rollback() -> None:
    buffer = MarketSnapshotBuffer()
    first = quote("600001.SH", timestamp(), price=10.0, volume=1000, amount=10000)
    buffer.prime((first,))
    with pytest.raises(SnapshotSequenceError, match="backwards"):
        buffer.update(
            (
                quote(
                    "600001.SH",
                    timestamp() - timedelta(seconds=1),
                    price=10.1,
                    volume=1100,
                    amount=11000,
                ),
            )
        )
    with pytest.raises(SnapshotSequenceError, match="cumulative"):
        buffer.update(
            (
                quote(
                    "600001.SH",
                    timestamp(second=10),
                    price=10.1,
                    volume=900,
                    amount=9000,
                ),
            )
        )


def rolling(code: str, change: float, velocity: float) -> RollingFeatures:
    return RollingFeatures(
        code=code,
        source_ts=timestamp(),
        change_pct=change,
        velocity_1m_pct=velocity,
        velocity_3m_pct=velocity * 1.5,
        velocity_5m_pct=velocity * 2,
        acceleration_pct=velocity / 2,
        volume_delta_1m=1000,
        amount_delta_1m=10000,
        volume_ratio_1m=1.5,
        amount_ratio_1m=1.8,
        intraday_high_break=True,
        high_3d_break=False,
        market_relative_strength=change - 1,
    )


def membership(code: str, sector_code: str, sector_type: str = "industry") -> SectorMembership:
    return SectorMembership(
        security=security(code),
        sector_code=sector_code,
        sector_name=f"板块{sector_code}",
        sector_type=sector_type,
        member_count=6,
        effective_date=date(2026, 7, 30),
        source_ts=timestamp(),
        received_ts=timestamp(),
        provider_version="pro-test",
        config_version="v1",
        quality=DataQuality.DEGRADED,
        source_timestamp_kind=SourceTimestampKind.RECEIVED_FALLBACK,
    )


def test_sector_engine_enforces_breadth_sync_and_candidate_rank() -> None:
    features = tuple(
        rolling(f"60000{index}.SH", change=7 - index * 0.5, velocity=1 - index * 0.05)
        for index in range(1, 7)
    )
    memberships = tuple(
        membership(feature.code, "I001")
        for feature in features
    )
    engine = SectorEngine()

    metrics = engine.calculate(features, memberships)
    first = engine.select_for_security(features[0].code, memberships, metrics)
    last = engine.select_for_security(features[-1].code, memberships, metrics)

    assert first is not None and first.metrics.gate_passed and first.gate_passed
    assert first.metrics.up_ratio == 1.0
    assert first.metrics.strong_count == 6
    assert last is not None and not last.gate_passed


def test_sector_engine_rewards_three_persistent_rounds_and_resets() -> None:
    features = tuple(
        rolling(f"60000{index}.SH", change=7 - index * 0.5, velocity=1.0)
        for index in range(1, 7)
    )
    memberships = tuple(membership(feature.code, "I001") for feature in features)
    engine = SectorEngine()

    first = engine.calculate(features, memberships)[("industry", "I001")].score
    engine.calculate(features, memberships)
    third = engine.calculate(features, memberships)[("industry", "I001")].score
    assert third == min(30.0, first + 1.0)

    engine.reset()
    after_reset = engine.calculate(features, memberships)[("industry", "I001")].score
    assert after_reset == first


def test_sector_engine_prefers_any_passing_membership_over_higher_nonpassing_one() -> None:
    target = rolling("600001.SH", change=3.0, velocity=0.8)
    industry_peers = (
        rolling("600002.SH", change=2.5, velocity=0.7),
        rolling("600003.SH", change=2.4, velocity=0.7),
    )
    concept_peers = tuple(
        rolling(f"00000{index}.SZ", change=9.0 - index, velocity=1.2)
        for index in range(1, 6)
    )
    features = (target, *industry_peers, *concept_peers)
    memberships = (
        membership(target.code, "I001", "industry"),
        *(membership(row.code, "I001", "industry") for row in industry_peers),
        membership(target.code, "C001", "concept"),
        *(membership(row.code, "C001", "concept") for row in concept_peers),
    )
    engine = SectorEngine()
    metrics = engine.calculate(features, memberships)
    selected = engine.select_for_security(target.code, memberships, metrics)
    assert selected is not None
    assert selected.gate_passed
    assert selected.metrics.sector_type == "industry"


def candidate_input(
    code: str,
    *,
    sector_code: str,
    sector_score: float,
    change: float,
    velocity: float,
) -> CandidateInput:
    return CandidateInput(
        security=security(code),
        price=10.0,
        change_pct=change,
        velocity_pct=velocity,
        sector=f"板块{sector_code}",
        sector_strength=sector_score,
        trend_3d_pct=2.0,
        source_ts=timestamp(),
        received_ts=timestamp(second=1),
        provider_version="native-test",
        config_version="v1",
        velocity_1m_pct=velocity,
        velocity_3m_pct=velocity * 1.5,
        velocity_5m_pct=velocity * 2,
        acceleration_pct=0.4,
        amount_ratio_1m=1.8,
        intraday_high_break=True,
        sector_code=sector_code,
        sector_gate_passed=True,
        sector_up_ratio=0.8,
        sector_strong_count=6,
        sector_rank_percentile=0.1,
        highs_rising_3d=True,
        lows_rising_3d=True,
        data_completeness=0.9,
    )


def test_candidate_engine_keeps_funds_optional_diversifies_and_fills_three() -> None:
    inputs = (
        candidate_input("600001.SH", sector_code="I1", sector_score=27, change=6, velocity=1.5),
        candidate_input("600002.SH", sector_code="I1", sector_score=26, change=5.5, velocity=1.4),
        candidate_input("600003.SH", sector_code="I1", sector_score=25, change=5.0, velocity=1.3),
        candidate_input("000001.SZ", sector_code="I2", sector_score=22, change=4.0, velocity=1.0),
    )
    batch = CandidateEngine().calculate(
        inputs,
        HealthState.HEALTHY,
        CandidateConfig("v1", "0.4"),
    )
    assert batch is not None
    assert len(batch.candidates) == 3
    assert [candidate.sector_code for candidate in batch.candidates].count("I1") == 2
    assert any(candidate.sector_code == "I2" for candidate in batch.candidates)
    assert all(candidate.fund_score == 0 for candidate in batch.candidates)
    assert all(candidate.fund_label == "资金未确认" for candidate in batch.candidates)
    assert batch.formal_count == 3
    assert not batch.overall_weak

    weak = CandidateEngine().calculate(
        inputs[:3],
        HealthState.HEALTHY,
        CandidateConfig("v1", "0.4"),
    )
    assert weak is not None and len(weak.candidates) == 3
    assert weak.formal_count == 2
    assert weak.candidates[-1].is_supplement
    assert weak.candidates[-1].level == "近"
    assert weak.overall_weak


def test_stable_top3_requires_three_small_leads_but_accepts_eight_points() -> None:
    inputs = tuple(
        candidate_input(
            f"60000{index}.SH",
            sector_code=f"I{index}",
            sector_score=25 - index,
            change=6 - index * 0.2,
            velocity=1.5,
        )
        for index in range(1, 5)
    )
    engine = CandidateEngine()
    config = CandidateConfig("v1", "0.4")
    baseline = engine.calculate(inputs[:3], HealthState.HEALTHY, config)
    replacement = engine.calculate(
        (inputs[0], inputs[1], inputs[3]),
        HealthState.HEALTHY,
        config,
    )
    assert baseline is not None and replacement is not None
    baseline_third = baseline.candidates[2]
    replacement_third = replace(
        replacement.candidates[2],
        level=baseline_third.level,
        total_score=baseline_third.total_score + 2,
        score=baseline_third.total_score + 2,
    )
    replacement = replace(
        replacement,
        candidates=(
            replacement.candidates[0],
            replacement.candidates[1],
            replacement_third,
        ),
    )
    selector = StableTop3Selector()
    assert selector.update(baseline).candidates == baseline.candidates
    assert selector.update(replacement).candidates[-1].code == baseline_third.code
    assert selector.update(replacement).candidates[-1].code == baseline_third.code
    assert selector.update(replacement).candidates[-1].code == replacement_third.code

    selector.reset()
    selector.update(baseline)
    immediate = replace(
        replacement,
        candidates=(
            replacement.candidates[0],
            replacement.candidates[1],
            replace(
                replacement_third,
                total_score=baseline_third.total_score + 8,
                score=baseline_third.total_score + 8,
            ),
        ),
    )
    assert selector.update(immediate).candidates[-1].code == replacement_third.code


def test_strong_movement_needs_two_rounds_and_allows_unconfirmed_funds() -> None:
    inputs = tuple(
        candidate_input(
            f"60000{index}.SH",
            sector_code=f"I{index}",
            sector_score=24,
            change=5,
            velocity=1.0,
        )
        for index in range(1, 4)
    )
    batch = CandidateEngine().calculate(
        inputs,
        HealthState.HEALTHY,
        CandidateConfig("v1", "0.4"),
    )
    assert batch is not None
    detector = StrongMovementDetector()
    assert detector.evaluate(batch) is None
    stronger = replace(
        batch,
        source_ts=batch.source_ts + timedelta(seconds=10),
        candidates=tuple(
            replace(
                candidate,
                velocity_pct=candidate.velocity_pct + 0.5,
                sector_score=candidate.sector_score + 1,
            )
            for candidate in batch.candidates
        ),
    )
    event = detector.evaluate(stronger)
    assert event is not None
    assert event.funds_unconfirmed
    assert len(event.triggering_codes) == 3


def test_fixed_alerts_fire_once_even_with_same_three_and_intraday_stays_limited() -> None:
    batch = CandidateEngine().calculate(
        tuple(
            candidate_input(
                f"60000{index}.SH",
                sector_code=f"I{index}",
                sector_score=24,
                change=5,
                velocity=1,
            )
            for index in range(1, 4)
        ),
        HealthState.HEALTHY,
        CandidateConfig("v1", "0.4"),
    )
    assert batch is not None
    policy = AlertPolicy()
    morning = timestamp().replace(hour=9, minute=45)
    assert policy.decide(batch, morning, AlertTrigger.SCHEDULED_0945).should_alert
    assert not policy.decide(batch, morning, AlertTrigger.SCHEDULED_0945).should_alert
    afternoon = morning.replace(hour=14)
    assert policy.decide(batch, afternoon, AlertTrigger.SCHEDULED_1445).should_alert

    schedule = MarketSessionSchedule()
    assert (
        schedule.crossed_fixed_trigger(
            morning - timedelta(seconds=10),
            morning + timedelta(seconds=50),
        )
        is AlertTrigger.SCHEDULED_0945
    )
    assert (
        schedule.crossed_fixed_trigger(
            morning + timedelta(minutes=1),
            morning + timedelta(minutes=2),
        )
        is None
    )


class StaticTransport:
    def __init__(self, records: tuple[dict[str, object], ...]) -> None:
        self.records = records

    def execute(self, request: object) -> TransportResult:
        now = timestamp()
        return TransportResult(
            records=self.records,  # type: ignore[arg-type]
            http_status=200,
            elapsed_seconds=1.0,
            provenance=ProviderProvenance(
                provider_profile="native_realtime",
                endpoint="tushare.realtime_quote:sina",
                provider_version="test",
                schema_version="v1",
                source_ts=now,
                received_ts=now,
                source_timestamp_kind=ProviderTimestampKind.SUPPLIER,
                freshness_seconds=0.0,
                quality=ProviderDataQuality.HEALTHY,
                degraded=False,
                fields_used=(),
            ),
        )


def realtime_record(code: str) -> dict[str, object]:
    now = timestamp()
    return {
        "ts_code": code,
        "name": code,
        "price": 10.1,
        "pre_close": 10.0,
        "open": 10.0,
        "high": 10.2,
        "low": 9.9,
        "vol": 1000,
        "amount": 10000,
        "source_ts": now.isoformat(),
        "received_ts": now.isoformat(),
        "data_quality": "HEALTHY",
    }


def test_full_market_scan_rejects_partial_round_and_health_needs_three_rounds() -> None:
    securities = tuple(
        security(code)
        for code in ("600001.SH", "600002.SH", "000001.SZ")
    )
    partial = FullMarketScanCoordinator(
        StaticTransport(tuple(realtime_record(item.code) for item in securities[:2])),
        clock=timestamp,
    )
    with pytest.raises(IncompleteScanError, match="coverage"):
        partial.fetch_once(securities)

    future_record = realtime_record(securities[0].code)
    future_record["source_ts"] = (timestamp() + timedelta(seconds=20)).isoformat()
    future = FullMarketScanCoordinator(
        StaticTransport((future_record,)),
        clock=timestamp,
    )
    with pytest.raises(IncompleteScanError, match="future"):
        future.fetch_once((securities[0],))

    tracker = DataHealthTracker()
    base = MarketScan(
        scan_id="scan",
        started_at=timestamp(),
        completed_at=timestamp(second=1),
        quotes=(),
        requested_count=100,
        coverage_ratio=0.99,
        duplicate_count=0,
        source_span_seconds=1,
        max_source_age_seconds=1,
        elapsed_seconds=1,
    )
    assert tracker.observe(base) is HealthState.WARMING
    assert tracker.observe(replace(base, scan_id="scan2")) is HealthState.WARMING
    assert tracker.observe(replace(base, scan_id="scan3")) is HealthState.HEALTHY
    stale = replace(base, scan_id="stale", max_source_age_seconds=70)
    assert tracker.observe(stale) is HealthState.STALE


class PassingTester:
    def test(self, profile: object, secret: str) -> CredentialTestResult:
        return CredentialTestResult(
            success=True,
            tested_at=timestamp(),
            status_text="通过",
            permission_summary="通过",
            expires_at="未知",
        )


def test_primary_token_migration_is_explicit_tested_and_keeps_legacy() -> None:
    store = MemoryCredentialStore()
    store.set(FAST_CREDENTIAL, "legacy-test-token")
    controller = DataSourceSettingsController(store=store, tester=PassingTester())
    assert not controller.migrate_legacy_fast(confirmed=False)
    assert store.get(PRIMARY_CREDENTIAL) is None
    assert controller.migrate_legacy_fast(confirmed=True)
    assert store.get(PRIMARY_CREDENTIAL) == "legacy-test-token"
    assert store.get(FAST_CREDENTIAL) == "legacy-test-token"
    assert controller.settings.mode is DataSourceMode.TUSHARE_15000


def test_alert_history_daily_summary_and_v3_items_are_persisted(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "watcher.sqlite3")
    batch = CandidateEngine().calculate(
        tuple(
            candidate_input(
                f"60000{index}.SH",
                sector_code=f"I{index}",
                sector_score=24,
                change=5,
                velocity=1,
            )
            for index in range(1, 4)
        ),
        HealthState.HEALTHY,
        CandidateConfig("v1", "0.4"),
    )
    assert batch is not None
    snapshot_id = store.record_batch(batch)
    now = timestamp().replace(hour=14, minute=45)
    store.record_alert_event(
        snapshot_id,
        now.isoformat(),
        "scheduled-14:45",
        "windows-desktop",
        "scheduled-14:45",
    )
    history = store.list_alert_history(now=now, days=30)
    assert len(history) == 1
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM candidate_items").fetchone() == (3,)
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (3,)

    summary = DailySummaryEngine().generate(
        trade_date=now.date(),
        generated_at=now.replace(hour=15, minute=30),
        alert_history=history,
        closing_prices={"600001.SH": 10.5},
        health_interruption_count=1,
    )
    store.record_daily_summary(summary.as_record())
    saved = store.get_daily_summary(now.date().isoformat())
    assert saved is not None
    assert saved["alert_count"] == 1
    assert "不预测" not in saved["summary_text"]
    assert "1 次数据延迟或中断" in saved["health_summary"]


def test_health_interruption_onsets_are_counted_by_local_date(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "health.sqlite3")
    store.record_health_metric(
        {
            "source_ts": timestamp().isoformat(),
            "received_ts": timestamp().isoformat(),
            "state": HealthState.STOPPED.value,
            "provider_version": "native-test",
            "config_version": "v1",
            "detail": "coverage",
        }
    )
    store.record_health_metric(
        {
            "source_ts": (timestamp() + timedelta(days=1)).isoformat(),
            "received_ts": (timestamp() + timedelta(days=1)).isoformat(),
            "state": HealthState.WARMING.value,
            "provider_version": "native-test",
            "config_version": "v1",
            "detail": "source-age",
        }
    )
    assert store.count_health_interruptions(timestamp().date().isoformat()) == 1


def test_post_close_review_forms_real_data_top3_without_inventing_minutes() -> None:
    codes = (
        "600001.SH",
        "600002.SH",
        "600003.SH",
        "000001.SZ",
        "000002.SZ",
        "000003.SZ",
    )
    stock_records = tuple(
        {
            "ts_code": code,
            "name": f"样本{index}",
            "industry": "行业甲" if index <= 3 else "行业乙",
            "list_date": "20200101",
        }
        for index, code in enumerate(codes, start=1)
    )
    open_dates = (
        date(2026, 7, 24),
        date(2026, 7, 27),
        date(2026, 7, 28),
        date(2026, 7, 29),
    )
    daily_by_date: dict[date, tuple[dict[str, object], ...]] = {}
    for day_index, day in enumerate(open_dates):
        daily_by_date[day] = tuple(
            {
                "ts_code": code,
                "trade_date": day.strftime("%Y%m%d"),
                "open": 10.0 + day_index * 0.1,
                "high": 10.5 + day_index * 0.1 + code_index * 0.05,
                "low": 9.9 + day_index * 0.1,
                "close": 10.3 + day_index * 0.1 + code_index * 0.04,
                "pre_close": 10.0 + day_index * 0.1,
                "pct_chg": 5.5 - code_index * 0.4 if day_index == 3 else 1.0,
                "vol": 1000 + day_index * 100,
                "amount": 10000 + day_index * 2000 + code_index * 100,
            }
            for code_index, code in enumerate(codes)
        )

    review = build_post_close_review(
        trade_date=open_dates[-1],
        generated_at=timestamp(),
        stock_records=stock_records,  # type: ignore[arg-type]
        daily_records_by_date=daily_by_date,  # type: ignore[arg-type]
        open_dates=open_dates,
        moneyflow_records=(
            {
                "ts_code": "600001.SH",
                "buy_elg_amount": 10.0,
                "sell_elg_amount": 5.0,
                "buy_lg_amount": 8.0,
                "sell_lg_amount": 4.0,
            },
        ),
    )

    assert len(review.top3) == 3
    assert all(candidate.retrospective_only for candidate in review.top3)
    assert all("分钟涨速" not in "；".join(candidate.reasons) for candidate in review.top3)
    assert max(
        sum(candidate.sector_code == sector for candidate in review.top3)
        for sector in {candidate.sector_code for candidate in review.top3}
    ) <= 2
    assert review.fund_capability == "daily_only"
    assert review.daily_summary_record()["version"] == "daily-summary-retrospective-v1"


def test_moneyflow_daily_records_never_become_intraday_fund_signal() -> None:
    result = FundEngine().probe(
        (
            {
                "ts_code": "600001.SH",
                "trade_date": "20260729",
                "buy_lg_amount": 10.0,
                "sell_lg_amount": 5.0,
                "buy_elg_amount": 4.0,
                "sell_elg_amount": 2.0,
            },
        )
    )
    assert result.capability is FundCapability.DAILY_ONLY
    assert FundEngine.unconfirmed().quality is DataQuality.UNAVAILABLE


def test_summary_is_due_after_1530_for_late_app_start() -> None:
    assert MarketSessionSchedule.summary_due(
        timestamp().replace(hour=15, minute=30)
    )
    assert MarketSessionSchedule.summary_due(
        timestamp().replace(hour=16, minute=5)
    )
    assert not MarketSessionSchedule.summary_due(
        timestamp().replace(hour=15, minute=29)
    )


class FakeV1Runtime:
    def __init__(self, universe: RuntimeUniverse, outcome: ScanOutcome) -> None:
        self.universe = universe
        self.outcome = outcome

    def scan_once(self) -> ScanOutcome:
        return self.outcome


class SequenceClock:
    def __init__(self, values: list[datetime]) -> None:
        self.values = values

    def __call__(self) -> datetime:
        return self.values.pop(0)


def test_v1_session_emits_0945_and_1445_even_when_top3_is_unchanged(
    tmp_path: Path,
) -> None:
    base = CandidateEngine().calculate(
        tuple(
            candidate_input(
                f"60000{index}.SH",
                sector_code=f"I{index}",
                sector_score=24,
                change=5,
                velocity=1,
            )
            for index in range(1, 4)
        ),
        HealthState.HEALTHY,
        CandidateConfig("v1", "0.4"),
    )
    assert base is not None
    morning = timestamp().replace(hour=9, minute=45)
    batch = replace(base, source_ts=morning, generated_at=morning)
    universe = RuntimeUniverse(
        profiles=(),
        memberships=(),
        trends={},
        high_3d={},
        open_dates=(morning.date(),),
        concept_loaded=False,
    )
    outcome = ScanOutcome(
        HealthState.HEALTHY,
        "正常",
        batch,
        batch,
        None,
        1.0,
        1.0,
        1.0,
        1.0,
    )
    fake = FakeV1Runtime(universe, outcome)
    credentials = MemoryCredentialStore()
    credentials.set(PRIMARY_CREDENTIAL, "test-token")
    clock = SequenceClock(
        [
            morning,
            morning + timedelta(seconds=1),
            morning + timedelta(seconds=10),
            morning + timedelta(seconds=11),
            morning.replace(hour=14),
            morning.replace(hour=14) + timedelta(seconds=1),
        ]
    )

    def factory(
        settings: DataSourceSettings,
        store: MemoryCredentialStore,
    ) -> tuple[TushareV1Runtime, Tushare15000Provider]:
        assert settings.mode is DataSourceMode.TUSHARE_15000
        assert store.get(PRIMARY_CREDENTIAL) == "test-token"
        return (
            cast(TushareV1Runtime, fake),
            cast(Tushare15000Provider, object()),
        )

    session = TushareV1Session(
        tmp_path / "session.sqlite3",
        credential_store=credentials,
        runtime_factory=factory,  # type: ignore[arg-type]
        clock=clock,
    )
    session.recover()
    first = session.consume_pending_alert()
    assert first is not None and first.title == "09:45 观察提醒"
    session.recover()
    assert session.consume_pending_alert() is None
    session.recover()
    afternoon = session.consume_pending_alert()
    assert afternoon is not None and afternoon.title == "14:45 观察提醒"

    restarted = TushareV1Session(
        tmp_path / "session.sqlite3",
        credential_store=credentials,
        runtime_factory=factory,  # type: ignore[arg-type]
        clock=SequenceClock([morning, morning + timedelta(seconds=1)]),
    )
    restarted.recover()
    assert restarted.consume_pending_alert() is None


def test_manual_fetch_updates_top3_persists_batch_and_always_emits_popup(
    tmp_path: Path,
) -> None:
    base = CandidateEngine().calculate(
        tuple(
            candidate_input(
                f"60000{index}.SH",
                sector_code=f"I{index}",
                sector_score=24,
                change=5,
                velocity=1,
            )
            for index in range(1, 4)
        ),
        HealthState.HEALTHY,
        CandidateConfig("v1", "0.4"),
    )
    assert base is not None
    first_at = timestamp().replace(hour=10, minute=5)
    batch = replace(base, source_ts=first_at, generated_at=first_at)
    universe = RuntimeUniverse(
        profiles=(),
        memberships=(),
        trends={},
        high_3d={},
        open_dates=(first_at.date(),),
        concept_loaded=False,
    )
    outcome = ScanOutcome(
        HealthState.HEALTHY,
        "正常",
        batch,
        batch,
        None,
        1.0,
        1.0,
        1.0,
        1.0,
    )
    fake = FakeV1Runtime(universe, outcome)
    credentials = MemoryCredentialStore()
    credentials.set(PRIMARY_CREDENTIAL, "test-token")

    def factory(
        _settings: DataSourceSettings,
        _store: MemoryCredentialStore,
    ) -> tuple[TushareV1Runtime, Tushare15000Provider]:
        return (
            cast(TushareV1Runtime, fake),
            cast(Tushare15000Provider, object()),
        )

    session = TushareV1Session(
        tmp_path / "manual.sqlite3",
        credential_store=credentials,
        runtime_factory=factory,  # type: ignore[arg-type]
        clock=SequenceClock(
            [
                first_at,
                first_at + timedelta(seconds=1),
                first_at + timedelta(minutes=1),
                first_at + timedelta(minutes=1, seconds=1),
            ]
        ),
    )

    session.manual_fetch()
    first = session.consume_pending_alert()
    session.manual_fetch()
    second = session.consume_pending_alert()

    assert session.supports_manual_fetch
    assert session.manual_fetch_label == "立即获取最新3只"
    assert first is not None and first.title == "当前最新3只"
    assert first.trigger_type == "manual"
    assert second is not None and second.title == "当前最新3只"
    with session.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM candidate_snapshots").fetchone() == (2,)
        assert connection.execute("SELECT COUNT(*) FROM alert_events").fetchone() == (0,)


def test_manual_fetch_failure_keeps_previous_top3_without_new_popup(
    tmp_path: Path,
) -> None:
    first_at = timestamp().replace(hour=11)
    universe = RuntimeUniverse(
        profiles=(),
        memberships=(),
        trends={},
        high_3d={},
        open_dates=(first_at.date(),),
        concept_loaded=False,
    )
    fake = FakeV1Runtime(
        universe,
        ScanOutcome(
            HealthState.STOPPED,
            "实时数据中断",
            None,
            None,
            None,
            None,
            None,
            failure_reason="provider",
        ),
    )
    credentials = MemoryCredentialStore()
    credentials.set(PRIMARY_CREDENTIAL, "test-token")
    session = TushareV1Session(
        tmp_path / "manual-failure.sqlite3",
        credential_store=credentials,
        runtime_factory=lambda _settings, _store: (
            cast(TushareV1Runtime, fake),
            cast(Tushare15000Provider, object()),
        ),
        clock=SequenceClock([first_at, first_at + timedelta(seconds=1)]),
    )

    session.manual_fetch()

    assert session.consume_pending_alert() is None
    assert session.state is HealthState.STOPPED
    with session.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM candidate_snapshots").fetchone() == (0,)


def test_optional_historical_minutes_does_not_block_realtime_top3() -> None:
    statuses = {
        capability: ProviderCapabilityStatus(
            capability,
            state=(
                ProviderCapabilityState.UNAVAILABLE
                if capability is ProviderCapability.HISTORICAL_MINUTES
                else ProviderCapabilityState.AVAILABLE
            ),
        )
        for capability in CAPABILITY_ORDER
    }

    assert _required_capabilities_ready(statuses)
