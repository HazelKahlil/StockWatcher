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
    StableTop3Config,
    StableTop3Selector,
    StrongMovementDetector,
    build_post_close_review,
)
from stock_watcher.providers.tushare import Tushare15000Provider
from stock_watcher.providers.tushare.capabilities import (
    CAPABILITY_ORDER,
    CapabilityCheckCoordinator,
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
    _realtime_capabilities_ready,
    _required_capabilities_ready,
    _visible_phase,
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


def test_snapshot_buffer_reuses_identical_supplier_event_across_scans() -> None:
    buffer = MarketSnapshotBuffer()
    first = quote(
        "600001.SH",
        timestamp(),
        price=10.0,
        volume=1000,
        amount=10000,
        scan_id="scan-1",
    )
    repeated = replace(
        first,
        received_ts=first.received_ts + timedelta(seconds=30),
        scan_id="scan-2",
    )

    buffer.update((first,))
    repeated_feature = buffer.update((repeated,))[0]

    assert repeated_feature.source_ts == first.source_ts
    assert repeated_feature.velocity_1m_pct is None
    with pytest.raises(SnapshotSequenceError, match="conflicting duplicate"):
        buffer.update((replace(repeated, price=10.1),))


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
    memberships = tuple(membership(feature.code, "I001") for feature in features)
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
        rolling(f"60000{index}.SH", change=7 - index * 0.5, velocity=1.0) for index in range(1, 7)
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
        rolling(f"00000{index}.SZ", change=9.0 - index, velocity=1.2) for index in range(1, 6)
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


def test_sector_engine_allows_concept_gate_when_industry_is_weak() -> None:
    target = rolling("600001.SH", change=8.0, velocity=1.0)
    weak_industry = (
        rolling("600002.SH", change=-1.0, velocity=-0.2),
        rolling("600003.SH", change=0.0, velocity=0.0),
    )
    strong_concept = tuple(
        rolling(f"00000{index}.SZ", change=6.0 - index * 0.2, velocity=1.2) for index in range(1, 6)
    )
    features = (target, *weak_industry, *strong_concept)
    memberships = (
        membership(target.code, "I001", "industry"),
        *(membership(row.code, "I001", "industry") for row in weak_industry),
        membership(target.code, "C001", "concept"),
        *(membership(row.code, "C001", "concept") for row in strong_concept),
    )
    engine = SectorEngine()
    metrics = engine.calculate(features, memberships)
    selected = engine.select_for_security(target.code, memberships, metrics)
    assert selected is not None
    assert selected.gate_passed
    assert selected.metrics.sector_type == "concept"


def test_sector_engine_prefers_highest_passing_concept_and_keeps_industry_on_tie() -> None:
    target = rolling("600001.SH", change=5.0, velocity=1.1)
    peers = tuple(
        rolling(f"00000{index}.SZ", change=5.0 - index * 0.1, velocity=1.0)
        for index in range(1, 10)
    )
    features = (target, *peers)
    memberships = (
        membership(target.code, "C001", "concept"),
        *(membership(row.code, "C001", "concept") for row in peers[:5]),
        membership(target.code, "C002", "concept"),
        *(membership(row.code, "C002", "concept") for row in peers[5:]),
    )
    engine = SectorEngine()
    metrics = engine.calculate(features, memberships)
    selected = engine.select_for_security(target.code, memberships, metrics)
    assert selected is not None and selected.gate_passed
    assert selected.metrics.sector_type == "concept"
    assert selected.metrics.sector_code == "C001"


def test_candidate_engine_uses_medium_threshold_instead_of_labeling_every_formal_row() -> None:
    weak = replace(
        candidate_input(
            "600001.SH",
            sector_code="I001",
            sector_score=5,
            change=0.5,
            velocity=0.1,
        ),
        amount_ratio_1m=None,
        volume_ratio_1m=None,
        acceleration_pct=0.0,
        data_completeness=0.9,
    )
    batch = CandidateEngine().calculate(
        (weak,),
        HealthState.HEALTHY,
        CandidateConfig("v1", "0.4"),
    )
    assert batch is not None and len(batch.candidates) == 1
    assert batch.candidates[0].is_formal
    assert batch.candidates[0].core_score < 32
    assert batch.candidates[0].level == "近"


def test_anomaly_detector_uses_formal_candidate_pool_beyond_displayed_top3() -> None:
    inputs = tuple(
        candidate_input(
            f"600{index:03d}.SH",
            sector_code=f"I{index:03d}",
            sector_score=24,
            change=5,
            velocity=1.0,
        )
        for index in range(1, 121)
    )
    engine = CandidateEngine()
    config = CandidateConfig("v1", "0.4")
    batch = engine.calculate(inputs, HealthState.HEALTHY, config)
    assert batch is not None and len(batch.candidates) == 3
    pool = engine.rank_formal_candidates(inputs, config)
    assert len(pool) == 120
    detector = StrongMovementDetector(candidate_pool_size=100)
    assert detector.evaluate(batch, candidate_pool=pool) is None

    outside_display = pool[49]
    accelerated = replace(
        outside_display,
        velocity_pct=outside_display.velocity_pct + 0.5,
        sector_score=outside_display.sector_score + 1.0,
        source_ts=outside_display.source_ts + timedelta(seconds=10),
    )
    event = detector.evaluate(
        replace(batch, source_ts=batch.source_ts + timedelta(seconds=10)),
        candidate_pool=(*pool[:49], accelerated, *pool[50:]),
    )
    assert event is not None
    assert outside_display.code in event.triggering_codes
    assert outside_display.code not in {candidate.code for candidate in batch.candidates}


def test_anomaly_detector_ignores_near_supplement_pool_rows() -> None:
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
    engine = CandidateEngine()
    config = CandidateConfig("v1", "0.4")
    batch = engine.calculate(inputs, HealthState.HEALTHY, config)
    assert batch is not None
    near = replace(batch.candidates[0], is_formal=False, is_supplement=True, level="近")
    detector = StrongMovementDetector()
    assert detector.evaluate(batch, candidate_pool=(near,)) is None
    assert (
        detector.evaluate(
            replace(batch, source_ts=batch.source_ts + timedelta(seconds=10)),
            candidate_pool=(replace(near, velocity_pct=near.velocity_pct + 2),),
        )
        is None
    )


def test_stable_top3_respects_configured_minimum_seat_hold_when_clock_is_supplied() -> None:
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
    selector = StableTop3Selector(
        StableTop3Config(minimum_seat_hold_seconds=60, confirmation_cycles=3)
    )
    start = timestamp()
    selector.update(baseline, now=start)
    for seconds in (10, 20, 30):
        held = selector.update(
            replace(
                replacement,
                source_ts=start + timedelta(seconds=seconds),
                generated_at=start + timedelta(seconds=seconds),
            ),
            now=start + timedelta(seconds=seconds),
        )
        assert tuple(row.code for row in held.candidates) == tuple(
            row.code for row in baseline.candidates
        )
    switched = selector.update(
        replace(
            replacement,
            source_ts=start + timedelta(seconds=61),
            generated_at=start + timedelta(seconds=61),
        ),
        now=start + timedelta(seconds=61),
    )
    assert tuple(row.code for row in switched.candidates) == tuple(
        row.code for row in replacement.candidates
    )


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
        sector_median_change_pct=1.2,
        sector_rank=1,
        sector_valid_count=10,
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
    assert all(candidate.sector_gate_passed for candidate in batch.candidates)
    assert all(candidate.sector_up_ratio == pytest.approx(0.8) for candidate in batch.candidates)
    assert all(candidate.sector_strong_count == 6 for candidate in batch.candidates)
    assert all(
        candidate.sector_median_change_pct == pytest.approx(1.2) for candidate in batch.candidates
    )
    assert all(candidate.sector_rank == 1 for candidate in batch.candidates)
    assert all(candidate.sector_valid_count == 10 for candidate in batch.candidates)
    assert batch.formal_count == 3
    assert not batch.overall_weak

    weak = CandidateEngine().calculate(
        inputs[:3],
        HealthState.HEALTHY,
        CandidateConfig("v1", "0.4"),
    )
    assert weak is not None and len(weak.candidates) == 2
    assert weak.formal_count == 2
    assert weak.overall_weak

    cold = tuple(
        replace(
            item,
            velocity_pct=0.0,
            velocity_1m_pct=None,
            velocity_3m_pct=None,
            velocity_5m_pct=None,
            acceleration_pct=None,
            volume_ratio_1m=None,
            amount_ratio_1m=None,
            data_completeness=0.3333,
        )
        for item in inputs
    )
    cold_batch = CandidateEngine().calculate(
        cold,
        HealthState.HEALTHY,
        CandidateConfig("v1", "0.4"),
    )
    assert cold_batch is not None
    assert len(cold_batch.candidates) == 3
    assert [candidate.sector_code for candidate in cold_batch.candidates].count("I1") == 2
    assert cold_batch.candidates[-1].sector_code == "I2"
    assert all(candidate.level == "近" for candidate in cold_batch.candidates)
    assert all(not candidate.velocity_available for candidate in cold_batch.candidates)
    assert all("1/3/5分钟涨速 —/—/—" in candidate.reasons for candidate in cold_batch.candidates)


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


def test_stable_top3_refreshes_held_rows_from_current_fresh_scan() -> None:
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
    refreshed_at = baseline_third.source_ts + timedelta(minutes=1)
    refreshed_third = replace(
        baseline_third,
        source_ts=refreshed_at,
        price=10.8,
        change_pct=8.0,
        reasons=("本轮新鲜字段",),
    )
    current = {
        baseline.candidates[0].code: baseline.candidates[0],
        baseline.candidates[1].code: baseline.candidates[1],
        refreshed_third.code: refreshed_third,
    }
    selector = StableTop3Selector()
    selector.update(baseline)

    held = selector.update(replacement, current_candidates=current)

    assert held.candidates[-1].code == baseline_third.code
    assert held.candidates[-1].source_ts == refreshed_at
    assert held.candidates[-1].price == 10.8
    assert held.candidates[-1].reasons == ("本轮新鲜字段",)


def test_stable_top3_drops_row_missing_from_current_fresh_scan() -> None:
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
    replacement = replace(
        replacement,
        candidates=(
            replacement.candidates[0],
            replacement.candidates[1],
            replace(
                replacement.candidates[2],
                level=baseline.candidates[2].level,
                total_score=baseline.candidates[2].total_score + 2,
                score=baseline.candidates[2].total_score + 2,
            ),
        ),
    )
    selector = StableTop3Selector()
    selector.update(baseline)
    current = {
        baseline.candidates[0].code: baseline.candidates[0],
        baseline.candidates[1].code: baseline.candidates[1],
    }

    selected = selector.update(replacement, current_candidates=current)

    assert tuple(row.code for row in selected.candidates) == tuple(
        row.code for row in replacement.candidates
    )


def test_refresh_stable_candidates_strictly_preserves_same_sector_limit() -> None:
    inputs = tuple(
        candidate_input(
            f"60000{index}.SH",
            sector_code="I1",
            sector_score=25 - index,
            change=6 - index * 0.2,
            velocity=1.5,
        )
        for index in range(1, 4)
    )
    engine = CandidateEngine()
    config = CandidateConfig("v1", "0.4")

    refreshed = engine.refresh_stable_candidates(
        inputs,
        tuple(item.security.code for item in inputs),
        config,
    )

    assert tuple(refreshed) == tuple(item.security.code for item in inputs[:2])
    assert all(row.is_formal for row in refreshed.values())


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


def realtime_record(
    code: str,
    *,
    at: datetime | None = None,
    quality: str = "HEALTHY",
) -> dict[str, object]:
    now = at or timestamp()
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
        "data_quality": quality,
    }


def test_full_market_scan_rejects_partial_round_and_recovery_needs_three_rounds() -> None:
    securities = tuple(security(code) for code in ("600001.SH", "600002.SH", "000001.SZ"))
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
    assert tracker.observe(base) is HealthState.HEALTHY
    assert tracker.fail() is HealthState.STOPPED
    assert tracker.observe(replace(base, scan_id="recovery1")) is HealthState.WARMING
    assert tracker.observe(replace(base, scan_id="recovery2")) is HealthState.WARMING
    assert tracker.observe(replace(base, scan_id="recovery3")) is HealthState.HEALTHY
    stale = replace(base, scan_id="stale", max_source_age_seconds=70)
    assert tracker.observe(stale) is HealthState.STALE
    assert tracker.observe(replace(base, scan_id="after-stale")) is HealthState.WARMING


def test_full_market_scan_excludes_six_stale_rows_and_keeps_real_timestamps() -> None:
    now = timestamp(second=30)
    fresh_at = now - timedelta(seconds=4)
    stale_at = now - timedelta(hours=2)
    securities = tuple(security(f"{index:06d}.SZ") for index in range(1, 5531))
    records = [realtime_record(item.code, at=fresh_at) for item in securities]
    for index in range(len(records) - 6, len(records)):
        records[index] = realtime_record(
            securities[index].code,
            at=stale_at,
            quality="STALE",
        )
    original_stale_timestamps = tuple(record["source_ts"] for record in records[-6:])

    scan = FullMarketScanCoordinator(
        StaticTransport(tuple(records)),
        clock=lambda: now,
    ).fetch_once(securities)

    assert len(scan.quotes) == 5524
    assert scan.coverage_ratio == pytest.approx(5524 / 5530)
    assert scan.stale_excluded_count == 6
    assert scan.unavailable_excluded_count == 0
    assert scan.excluded_count == 6
    assert scan.max_source_age_seconds == 4
    assert scan.source_span_seconds == 0
    assert all(quote.source_ts == fresh_at for quote in scan.quotes)
    assert tuple(record["source_ts"] for record in records[-6:]) == (original_stale_timestamps)


def test_full_market_scan_fails_when_fresh_coverage_is_below_99_percent() -> None:
    now = timestamp(second=30)
    securities = tuple(security(f"{index:06d}.SZ") for index in range(1, 101))
    records = [realtime_record(item.code, at=now) for item in securities]
    for index in (-1, -2):
        records[index] = realtime_record(
            securities[index].code,
            at=now - timedelta(minutes=10),
            quality="STALE",
        )

    coordinator = FullMarketScanCoordinator(
        StaticTransport(tuple(records)),
        clock=lambda: now,
    )

    with pytest.raises(IncompleteScanError, match="coverage 0.9800"):
        coordinator.fetch_once(securities)


def test_excluded_stale_row_does_not_seed_rolling_baseline() -> None:
    first_at = timestamp()
    second_at = first_at + timedelta(minutes=1)
    securities = tuple(security(f"{index:06d}.SZ") for index in range(1, 102))
    stale_code = securities[-1].code
    first_records = [realtime_record(item.code, at=first_at) for item in securities]
    first_records[-1] = realtime_record(
        stale_code,
        at=first_at - timedelta(minutes=10),
        quality="STALE",
    )
    first_scan = FullMarketScanCoordinator(
        StaticTransport(tuple(first_records)),
        clock=lambda: first_at,
    ).fetch_once(securities)
    second_scan = FullMarketScanCoordinator(
        StaticTransport(tuple(realtime_record(item.code, at=second_at) for item in securities)),
        clock=lambda: second_at,
    ).fetch_once(securities)
    buffer = MarketSnapshotBuffer()

    buffer.update(first_scan.quotes)
    second_features = {feature.code: feature for feature in buffer.update(second_scan.quotes)}

    assert second_features[securities[0].code].velocity_1m_pct == 0
    assert second_features[stale_code].velocity_1m_pct is None


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
        assert connection.execute("SELECT version FROM schema_version").fetchone() == (9,)

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
    assert (
        max(
            sum(candidate.sector_code == sector for candidate in review.top3)
            for sector in {candidate.sector_code for candidate in review.top3}
        )
        <= 2
    )
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
    assert MarketSessionSchedule.summary_due(timestamp().replace(hour=15, minute=30))
    assert MarketSessionSchedule.summary_due(timestamp().replace(hour=16, minute=5))
    assert not MarketSessionSchedule.summary_due(timestamp().replace(hour=15, minute=29))


def test_session_schedules_30_day_history_prune_once_per_day(tmp_path: Path) -> None:
    now = timestamp().replace(hour=10, minute=15)
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
    credentials = MemoryCredentialStore()
    session = TushareV1Session(
        tmp_path / "history-prune.sqlite3",
        credential_store=credentials,
        runtime_factory=lambda _settings, _store: (
            cast(TushareV1Runtime, object()),
            cast(Tushare15000Provider, object()),
        ),
        clock=lambda: now,
    )
    old = replace(
        batch,
        source_ts=now - timedelta(days=31),
        generated_at=now - timedelta(days=31),
    )
    snapshot_id = session.store.record_batch(old)
    session.store.record_alert_event(
        snapshot_id,
        (now - timedelta(days=31)).isoformat(),
        "intraday",
        "macos-desktop",
        "intraday",
    )

    session._prune_history_if_due(now)
    session._prune_history_if_due(now + timedelta(hours=1))

    assert session._history_pruned_date == now.date()
    assert session.store.list_alert_history(now=now, days=60) == []


class FakeV1Runtime:
    def __init__(self, universe: RuntimeUniverse, outcome: ScanOutcome) -> None:
        self.universe = universe
        self.outcome = outcome
        self.scan_calls = 0

    def scan_once(self) -> ScanOutcome:
        self.scan_calls += 1
        return self.outcome


class SequenceV1Runtime(FakeV1Runtime):
    def __init__(
        self,
        universe: RuntimeUniverse,
        outcomes: list[ScanOutcome],
    ) -> None:
        super().__init__(universe, outcomes[-1])
        self.outcomes = outcomes

    def scan_once(self) -> ScanOutcome:
        self.scan_calls += 1
        return self.outcomes.pop(0)


class SequenceClock:
    def __init__(self, values: list[datetime]) -> None:
        self.values = values

    def __call__(self) -> datetime:
        return self.values.pop(0)


def test_visible_phase_does_not_call_1113_non_trading() -> None:
    assert _visible_phase(timestamp().replace(hour=11, minute=13)) == "上午盘中观察"
    assert _visible_phase(timestamp().replace(hour=12, minute=0)) == "午间休市"
    assert _visible_phase(timestamp().replace(hour=15, minute=1)) == "已收盘"


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
    assert fake.scan_calls == 3

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


def test_manual_fetch_completes_three_fresh_rounds_in_one_user_action(
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
    now = timestamp().replace(hour=10, minute=20)
    batch = replace(base, source_ts=now, generated_at=now)
    universe = RuntimeUniverse(
        profiles=(),
        memberships=(),
        trends={},
        high_3d={},
        open_dates=(now.date(),),
        concept_loaded=False,
    )
    warming = ScanOutcome(
        HealthState.WARMING,
        "实时数据恢复预热",
        None,
        None,
        None,
        7.0,
        1.0,
        1.0,
        6.0,
    )
    healthy = ScanOutcome(
        HealthState.HEALTHY,
        "全市场覆盖 100.0%，本轮 7.0 秒。",
        batch,
        batch,
        None,
        7.0,
        1.0,
        1.0,
        6.0,
    )
    fake = SequenceV1Runtime(universe, [warming, warming, healthy])
    credentials = MemoryCredentialStore()
    credentials.set(PRIMARY_CREDENTIAL, "test-token")
    clock_values = [now + timedelta(seconds=index) for index in range(6)]
    session = TushareV1Session(
        tmp_path / "manual-three-rounds.sqlite3",
        credential_store=credentials,
        runtime_factory=lambda _settings, _store: (
            cast(TushareV1Runtime, fake),
            cast(Tushare15000Provider, object()),
        ),
        clock=SequenceClock(clock_values),
    )

    session.manual_fetch()

    assert fake.scan_calls == 3
    assert session.state is HealthState.HEALTHY
    assert session.candidate_gate_label == "3只观察"
    alert = session.consume_pending_alert()
    assert alert is not None and alert.title == "当前最新3只"


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
    assert _realtime_capabilities_ready(statuses)


def test_static_capability_429_does_not_block_cached_manual_realtime_scan(
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
    now = timestamp().replace(hour=10, minute=15)
    batch = replace(base, source_ts=now, generated_at=now)
    universe = RuntimeUniverse(
        profiles=(),
        memberships=(),
        trends={},
        high_3d={},
        open_dates=(now.date(),),
        concept_loaded=False,
    )
    fake = FakeV1Runtime(
        universe,
        ScanOutcome(
            HealthState.HEALTHY,
            "正常",
            batch,
            batch,
            None,
            7.0,
            1.0,
            1.0,
            6.0,
        ),
    )
    statuses = {
        capability: ProviderCapabilityStatus(
            capability,
            state=(
                ProviderCapabilityState.RATE_LIMITED
                if capability is ProviderCapability.STOCK_LIST
                else ProviderCapabilityState.AVAILABLE
                if capability
                in {
                    ProviderCapability.REALTIME_1,
                    ProviderCapability.REALTIME_100,
                    ProviderCapability.REALTIME_300,
                    ProviderCapability.REALTIME_800,
                }
                else ProviderCapabilityState.UNKNOWN
            ),
        )
        for capability in CAPABILITY_ORDER
    }

    class StaticCapabilities:
        def seed_realtime_codes(self, _codes: object) -> None:
            return

        def statuses(self) -> dict[ProviderCapability, ProviderCapabilityStatus]:
            return statuses

        def start_background(self) -> bool:
            return False

        def start_realtime_background(self) -> bool:
            return False

        def shutdown(self) -> None:
            return

    credentials = MemoryCredentialStore()
    credentials.set(PRIMARY_CREDENTIAL, "test-token")
    session = TushareV1Session(
        tmp_path / "cached-manual.sqlite3",
        credential_store=credentials,
        runtime_factory=lambda _settings, _store: (
            cast(TushareV1Runtime, fake),
            cast(Tushare15000Provider, object()),
        ),
        capability_checks=cast(CapabilityCheckCoordinator, StaticCapabilities()),
        clock=SequenceClock([now, now + timedelta(seconds=8)]),
    )
    session._capability_checks_required = True

    session.manual_fetch()

    assert fake.scan_calls == 1
    alert = session.consume_pending_alert()
    assert alert is not None and alert.title == "当前最新3只"


def test_cached_session_scans_while_realtime_probe_progresses(
    tmp_path: Path,
) -> None:
    now = timestamp().replace(hour=10, minute=15)
    universe = RuntimeUniverse(
        profiles=(),
        memberships=(),
        trends={},
        high_3d={},
        open_dates=(now.date(),),
        concept_loaded=False,
    )
    fake = FakeV1Runtime(
        universe,
        ScanOutcome(
            HealthState.HEALTHY,
            "正常",
            None,
            None,
            None,
            7.0,
            1.0,
            1.0,
            6.0,
        ),
    )
    statuses = {capability: ProviderCapabilityStatus(capability) for capability in CAPABILITY_ORDER}

    class RealtimeProgression:
        starts = 0

        def seed_realtime_codes(self, _codes: object) -> None:
            return

        def statuses(self) -> dict[ProviderCapability, ProviderCapabilityStatus]:
            return statuses

        def start_background(self) -> bool:
            raise AssertionError("交易时段不能启动普通Pro能力检查")

        def start_realtime_background(self) -> bool:
            self.starts += 1
            return True

        def shutdown(self) -> None:
            return

    credentials = MemoryCredentialStore()
    credentials.set(PRIMARY_CREDENTIAL, "test-token")
    checks = RealtimeProgression()
    session = TushareV1Session(
        tmp_path / "progressive-realtime.sqlite3",
        credential_store=credentials,
        runtime_factory=lambda _settings, _store: (
            cast(TushareV1Runtime, fake),
            cast(Tushare15000Provider, object()),
        ),
        capability_checks=cast(CapabilityCheckCoordinator, checks),
        clock=SequenceClock([now, now]),
    )
    session._capability_checks_required = True

    session.manual_fetch()

    assert checks.starts == 1
    assert fake.scan_calls == 1
    assert session.data_gate_label == "运行正常"
    assert session.candidate_gate_label == "无新结果"


def test_manual_fetch_does_not_wait_for_stalled_capability_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timestamp().replace(hour=10, minute=15)
    universe = RuntimeUniverse(
        profiles=(),
        memberships=(),
        trends={},
        high_3d={},
        open_dates=(now.date(),),
        concept_loaded=False,
    )
    fake = FakeV1Runtime(
        universe,
        ScanOutcome(
            HealthState.HEALTHY,
            "不应执行扫描",
            None,
            None,
            None,
            7.0,
            1.0,
            1.0,
            6.0,
        ),
    )
    statuses = {capability: ProviderCapabilityStatus(capability) for capability in CAPABILITY_ORDER}

    class StalledRealtimeProgression:
        in_flight = True

        def seed_realtime_codes(self, _codes: object) -> None:
            return

        def statuses(self) -> dict[ProviderCapability, ProviderCapabilityStatus]:
            return statuses

        def start_background(self) -> bool:
            raise AssertionError("交易时段不能启动普通Pro能力检查")

        def start_realtime_background(self) -> bool:
            return False

        def shutdown(self) -> None:
            return

    credentials = MemoryCredentialStore()
    credentials.set(PRIMARY_CREDENTIAL, "test-token")
    session = TushareV1Session(
        tmp_path / "manual-timeout.sqlite3",
        credential_store=credentials,
        runtime_factory=lambda _settings, _store: (
            cast(TushareV1Runtime, fake),
            cast(Tushare15000Provider, object()),
        ),
        capability_checks=cast(
            CapabilityCheckCoordinator,
            StalledRealtimeProgression(),
        ),
        clock=lambda: now,
    )
    session._capability_checks_required = True
    monotonic_values = iter((0.0, 30.0, 61.0))
    monkeypatch.setattr(
        "stock_watcher.ui.tushare_v1_session.monotonic_time",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(
        "stock_watcher.ui.tushare_v1_session.sleep_seconds",
        lambda _seconds: None,
    )

    session.manual_fetch()

    assert fake.scan_calls == 1
    assert session.data_gate_label == "运行正常"
    assert session.candidate_gate_label == "无新结果"
    assert session.last_fetch_detail == "不应执行扫描"
