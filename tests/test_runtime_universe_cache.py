from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from stock_watcher.domain import (
    DataQuality,
    HealthState,
    SectorMembership,
    Security,
    SourceTimestampKind,
)
from stock_watcher.engine import FundCapability, SecurityProfile, ThreeDayTrend
from stock_watcher.providers.tushare.errors import (
    ProviderError,
    ProviderFailureReason,
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
    FullMarketScanCoordinator,
    RuntimeUniverse,
    RuntimeUniverseCache,
    TushareBootstrapLoader,
    TushareV1Runtime,
    UniverseCacheError,
    UniverseCacheFailure,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 31, 9, 40, tzinfo=SHANGHAI)


def _security(index: int) -> Security:
    code = f"{index:06d}.SZ"
    return Security(code, f"缓存样本{index:06d}", "SZ")


def _universe() -> RuntimeUniverse:
    securities = tuple(_security(index) for index in range(1, 121))
    profiles = tuple(
        SecurityProfile(security=item, listed_trading_days=999)
        for item in securities
    )
    memberships = tuple(
        SectorMembership(
            security=item,
            sector_code=f"I{(index - 1) // 30 + 1}",
            sector_name=f"行业{(index - 1) // 30 + 1}",
            sector_type="industry",
            member_count=30,
            effective_date=NOW.date(),
            source_ts=NOW,
            received_ts=NOW,
            provider_version="pro-cache-test",
            config_version="runtime-universe-v1",
            quality=DataQuality.DEGRADED,
            source_timestamp_kind=SourceTimestampKind.RECEIVED_FALLBACK,
        )
        for index, item in enumerate(securities, start=1)
    )
    trends = {
        item.code: ThreeDayTrend(
            cumulative_change_pct=1.2,
            highs_rising=True,
            lows_rising=True,
            amount_rising=True,
            highest_price=10.5,
        )
        for item in securities
    }
    open_dates = (
        date(2026, 7, 24),
        date(2026, 7, 27),
        date(2026, 7, 28),
        date(2026, 7, 29),
        date(2026, 7, 30),
        date(2026, 7, 31),
        date(2026, 8, 3),
    )
    return RuntimeUniverse(
        profiles=profiles,
        memberships=memberships,
        trends=trends,
        high_3d={item.code: 10.5 for item in securities},
        open_dates=open_dates,
        concept_loaded=False,
        generated_at=NOW,
        trend_through_date=date(2026, 7, 30),
    )


def _realtime_result(universe: RuntimeUniverse) -> TransportResult:
    records: tuple[dict[str, str | int | float | bool | None], ...] = tuple(
        {
            "ts_code": security.code,
            "name": security.name,
            "price": 10.6,
            "pre_close": 10.0,
            "open": 10.0,
            "high": 10.7,
            "low": 9.9,
            "vol": 1000.0,
            "amount": 10000.0,
            "source_ts": NOW.isoformat(),
            "received_ts": NOW.isoformat(),
            "data_quality": "HEALTHY",
        }
        for security in universe.securities
    )
    return TransportResult(
        records=records,
        http_status=200,
        elapsed_seconds=1.0,
        provenance=ProviderProvenance(
            provider_profile="native_realtime",
            endpoint="tushare.realtime_quote:sina",
            provider_version="native-test",
            schema_version="native-realtime-v1",
            source_ts=NOW,
            received_ts=NOW,
            source_timestamp_kind=ProviderTimestampKind.SUPPLIER,
            freshness_seconds=0.0,
            quality=ProviderDataQuality.HEALTHY,
            degraded=False,
            fields_used=(),
        ),
    )


class _ForbiddenLoader:
    calls = 0

    def load(self) -> RuntimeUniverse:
        self.calls += 1
        raise AssertionError("ordinary Pro must not run inside scan_once")


class _RecordingRealtime:
    def __init__(self, result: TransportResult) -> None:
        self.result = result
        self.calls = 0

    def execute(self, _request: object) -> TransportResult:
        self.calls += 1
        return self.result


class _BatchedBootstrapProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.open_dates: tuple[date, ...] = (
            date(2026, 7, 24),
            date(2026, 7, 27),
            date(2026, 7, 28),
            date(2026, 7, 29),
            date(2026, 7, 30),
            date(2026, 7, 31),
            date(2026, 8, 3),
        )

    def stock_list(self, **_params: object) -> TransportResult:
        self.calls.append("stock_basic")
        return _transport_result(
            tuple(
                {
                    "ts_code": f"{index:06d}.SZ",
                    "name": f"真实样本{index:06d}",
                    "industry": f"行业{(index - 1) // 30 + 1}",
                    "market": "主板",
                    "list_date": (
                        "20260729" if index == 1 else "20100101"
                    ),
                    "list_status": "L",
                }
                for index in range(1, 121)
            )
        )

    def sector_classification(self, **_params: object) -> TransportResult:
        raise AssertionError("index_classify must not block the fast daily bootstrap")

    def sector_components(self, **_params: object) -> TransportResult:
        raise AssertionError("index_member_all must not block the fast daily bootstrap")

    def trading_dates(self, **_params: object) -> TransportResult:
        raise AssertionError("trade_cal must not block the fast daily bootstrap")

    def daily_bars(self, **params: object) -> TransportResult:
        compact = str(params["trade_date"])
        self.calls.append(f"daily:{compact}")
        trading_day = datetime.strptime(compact, "%Y%m%d").date()
        if trading_day not in self.open_dates:
            return _transport_result(())
        rows: list[dict[str, str | int | float | bool | None]] = []
        for index in range(1, 121):
            # Code 1 appears on only two completed days and remains excluded as
            # a new/incomplete listing. Code 2 is absent from the latest day and
            # is conservatively marked as a resumption/mechanical jump.
            if index == 1 and trading_day < date(2026, 7, 29):
                continue
            if index == 2 and trading_day == date(2026, 7, 30):
                continue
            rows.append(
                {
                    "ts_code": f"{index:06d}.SZ",
                    "trade_date": compact,
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.9,
                    "close": 10.1,
                    "pre_close": 10.0,
                    "vol": 1000.0,
                    "amount": 10000.0,
                }
            )
        rows.append(
            {
                "ts_code": "999999.SZ",
                "trade_date": compact,
                "open": 10.0,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "pre_close": 10.0,
                "vol": 1000.0,
                "amount": 10000.0,
            }
        )
        return _transport_result(tuple(rows))


def _transport_result(
    records: tuple[dict[str, str | int | float | bool | None], ...],
) -> TransportResult:
    return TransportResult(
        records=records,
        http_status=200,
        elapsed_seconds=0.1,
        provenance=ProviderProvenance(
            provider_profile="fast-primary",
            endpoint="/daily",
            provider_version="tushare-sdk-pro-route-v1",
            schema_version="v1",
            source_ts=None,
            received_ts=NOW,
            source_timestamp_kind=ProviderTimestampKind.MISSING,
            freshness_seconds=None,
            quality=ProviderDataQuality.DEGRADED,
            degraded=True,
            fields_used=(),
        ),
    )


def test_batched_bootstrap_builds_universe_and_industry_without_sector_routes() -> None:
    provider = _BatchedBootstrapProvider()
    universe = TushareBootstrapLoader(
        cast(object, provider),  # type: ignore[arg-type]
        minimum_profile_count=100,
        clock=lambda: NOW,
    ).load()

    assert len(universe.profiles) == 120
    assert len(universe.memberships) == 120
    assert len({membership.sector_name for membership in universe.memberships}) == 4
    assert not universe.concept_loaded
    assert universe.fund_capability.capability is FundCapability.UNAVAILABLE
    by_code = {profile.security.code: profile for profile in universe.profiles}
    assert by_code["000001.SZ"].listed_trading_days == 3
    assert by_code["000002.SZ"].is_corporate_action_day
    assert by_code["000003.SZ"].listed_trading_days == 999
    assert "999999.SZ" not in universe.trends
    assert "999999.SZ" not in universe.high_3d
    assert provider.calls == [
        "stock_basic",
        "daily:20260730",
        "daily:20260729",
        "daily:20260728",
        "daily:20260727",
    ]
    assert universe.open_dates == (
        date(2026, 7, 27),
        date(2026, 7, 28),
        date(2026, 7, 29),
        date(2026, 7, 30),
        date(2026, 7, 31),
    )


def test_bootstrap_merges_complete_concept_memberships_for_realtime_selection() -> None:
    class WithConcepts(_BatchedBootstrapProvider):
        def concept_classification(self, **_params: object) -> TransportResult:
            return _transport_result(
                (
                    {
                        "ts_code": "C001",
                        "name": "概念样本",
                        "idx_count": 6,
                    },
                )
            )

        def concept_components(self, **_params: object) -> TransportResult:
            return _transport_result(
                tuple(
                    {"ts_code": "C001", "con_code": f"{index:06d}.SZ"}
                    for index in range(1, 7)
                )
            )

    universe = TushareBootstrapLoader(
        cast(object, WithConcepts()),  # type: ignore[arg-type]
        minimum_profile_count=100,
        clock=lambda: NOW,
    ).load()

    concepts = tuple(
        membership
        for membership in universe.memberships
        if membership.sector_type == "concept"
    )
    assert universe.concept_loaded
    assert len(concepts) == 6
    assert {membership.sector_code for membership in concepts} == {"C001"}
    assert {membership.security.code for membership in concepts} == {
        f"{index:06d}.SZ" for index in range(1, 7)
    }


def test_fast_daily_bootstrap_skips_weekends_and_empty_weekday_holidays() -> None:
    provider = _BatchedBootstrapProvider()
    provider.open_dates = (
        date(2026, 7, 27),
        date(2026, 7, 28),
        date(2026, 7, 29),
        date(2026, 7, 31),
        date(2026, 8, 3),
    )
    monday_morning = NOW.replace(month=8, day=3)

    universe = TushareBootstrapLoader(
        cast(object, provider),  # type: ignore[arg-type]
        minimum_profile_count=100,
        clock=lambda: monday_morning,
    ).load()

    assert universe.trend_through_date == date(2026, 7, 31)
    assert universe.open_dates == (
        date(2026, 7, 27),
        date(2026, 7, 28),
        date(2026, 7, 29),
        date(2026, 7, 31),
        date(2026, 8, 3),
    )
    assert provider.calls == [
        "stock_basic",
        "daily:20260731",
        "daily:20260730",
        "daily:20260729",
        "daily:20260728",
        "daily:20260727",
    ]


def test_bootstrap_retry_keeps_successful_stock_batch_in_memory() -> None:
    class OneRateLimit(_BatchedBootstrapProvider):
        limited = False

        def daily_bars(self, **params: object) -> TransportResult:
            compact = str(params["trade_date"])
            if compact == "20260730" and not self.limited:
                self.limited = True
                self.calls.append(f"daily:{compact}")
                raise ProviderError(
                    ProviderFailureReason.RATE_LIMITED,
                    retry_after_seconds=60.0,
                )
            return super().daily_bars(**params)

    provider = OneRateLimit()
    loader = TushareBootstrapLoader(
        cast(object, provider),  # type: ignore[arg-type]
        minimum_profile_count=100,
        clock=lambda: NOW,
    )

    with pytest.raises(ProviderError) as captured:
        loader.load()
    assert captured.value.reason is ProviderFailureReason.RATE_LIMITED

    universe = loader.load()

    assert len(universe.profiles) == 120
    assert provider.calls.count("stock_basic") == 1
    assert provider.calls.count("daily:20260730") == 2


def test_bootstrap_rejects_daily_rows_from_the_wrong_or_future_date() -> None:
    class WrongDateProvider(_BatchedBootstrapProvider):
        def daily_bars(self, **params: object) -> TransportResult:
            result = super().daily_bars(**params)
            if not result.records:
                return result
            wrong = tuple(
                {**record, "trade_date": "20260803"}
                for record in result.records
            )
            return replace(result, records=wrong)

    loader = TushareBootstrapLoader(
        cast(object, WrongDateProvider()),  # type: ignore[arg-type]
        minimum_profile_count=100,
        clock=lambda: NOW,
    )

    with pytest.raises(RuntimeError, match="日线响应日期不匹配"):
        loader.load()


def test_bootstrap_marks_adjustment_and_resumption_codes_as_mechanical_jumps() -> None:
    class CorporateActionProvider(_BatchedBootstrapProvider):
        def adjustment_factors(self, **params: object) -> TransportResult:
            factor = 1.1 if params["trade_date"] == "20260731" else 1.0
            return _transport_result(
                ({"ts_code": "000003.SZ", "adj_factor": factor},)
            )

        def suspension_events(self, **_params: object) -> TransportResult:
            return _transport_result(({"ts_code": "000004.SZ"},))

    universe = TushareBootstrapLoader(
        cast(object, CorporateActionProvider()),  # type: ignore[arg-type]
        minimum_profile_count=100,
        clock=lambda: NOW,
    ).load()
    by_code = {profile.security.code: profile for profile in universe.profiles}

    assert by_code["000003.SZ"].is_corporate_action_day
    assert by_code["000004.SZ"].is_corporate_action_day


def test_default_universe_cache_rejects_a_truncated_market(tmp_path: Path) -> None:
    cache = RuntimeUniverseCache(tmp_path / "truncated-market.json")

    with pytest.raises(UniverseCacheError) as captured:
        cache.save(_universe())

    assert captured.value.reason is UniverseCacheFailure.INCOMPLETE


def test_runtime_universe_cache_round_trip_and_failed_replace_keeps_old_file(
    tmp_path: Path,
) -> None:
    cache = RuntimeUniverseCache(
        tmp_path / "runtime-universe-v1.json",
        minimum_profile_count=100,
    )
    universe = _universe()

    cache.save(universe)
    original = cache.path.read_bytes()
    loaded = cache.load(now=NOW + timedelta(minutes=5))

    assert loaded.securities == universe.securities
    assert loaded.trend_through_date == date(2026, 7, 30)
    assert loaded.generated_at == NOW
    assert not cache.path.with_suffix(".json.tmp").exists()

    with pytest.raises(UniverseCacheError) as captured:
        cache.save(replace(universe, profiles=universe.profiles[:10]))

    assert captured.value.reason is UniverseCacheFailure.INCOMPLETE
    assert cache.path.read_bytes() == original


def test_runtime_universe_cache_rejects_checksum_damage_and_stale_context(
    tmp_path: Path,
) -> None:
    cache = RuntimeUniverseCache(
        tmp_path / "runtime-universe-v1.json",
        minimum_profile_count=100,
    )
    cache.save(_universe())
    payload = json.loads(cache.path.read_text(encoding="utf-8"))
    payload["universe"]["profiles"][0]["security"]["name"] = "被篡改"
    cache.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UniverseCacheError) as corrupt:
        cache.load(now=NOW)
    assert corrupt.value.reason is UniverseCacheFailure.CORRUPT

    cache.save(_universe())
    with pytest.raises(UniverseCacheError) as stale:
        cache.load(now=datetime(2026, 8, 3, 9, 40, tzinfo=SHANGHAI))
    assert stale.value.reason is UniverseCacheFailure.STALE


def test_scan_uses_verified_cache_and_never_calls_ordinary_pro(
    tmp_path: Path,
) -> None:
    universe = _universe()
    cache = RuntimeUniverseCache(
        tmp_path / "runtime-universe-v1.json",
        minimum_profile_count=100,
    )
    cache.save(universe)
    loader = _ForbiddenLoader()
    realtime = _RecordingRealtime(_realtime_result(universe))
    runtime = TushareV1Runtime(
        cast(TushareBootstrapLoader, loader),
        FullMarketScanCoordinator(realtime, clock=lambda: NOW),
        universe_cache=cache,
        clock=lambda: NOW,
    )

    outcome = runtime.scan_once()

    assert outcome.health is HealthState.HEALTHY
    assert outcome.coverage_ratio == 1.0
    assert outcome.batch is not None
    assert len(outcome.batch.candidates) == 3
    assert outcome.batch.overall_weak
    assert all(candidate.level == "近" for candidate in outcome.batch.candidates)
    assert all(
        candidate.data_completeness == pytest.approx(0.3333)
        for candidate in outcome.batch.candidates
    )
    assert loader.calls == 0
    assert realtime.calls == 1


def test_stable_top3_cannot_retain_row_excluded_from_current_scan(
    tmp_path: Path,
) -> None:
    universe = _universe()
    cache = RuntimeUniverseCache(
        tmp_path / "runtime-universe-v1.json",
        minimum_profile_count=100,
    )
    cache.save(universe)
    first_result = _realtime_result(universe)

    class SequenceRealtime:
        def __init__(self) -> None:
            self.results = [first_result]

        def execute(self, _request: object) -> TransportResult:
            return self.results.pop(0)

    realtime = SequenceRealtime()
    runtime = TushareV1Runtime(
        cast(TushareBootstrapLoader, _ForbiddenLoader()),
        FullMarketScanCoordinator(realtime, clock=lambda: NOW),
        universe_cache=cache,
        clock=lambda: NOW,
    )
    first = runtime.scan_once()
    assert first.batch is not None
    stale_code = first.batch.candidates[-1].code
    realtime.results.append(
        replace(
            first_result,
            records=tuple(
                (
                    {**record, "data_quality": "STALE"}
                    if record["ts_code"] == stale_code
                    else record
                )
                for record in first_result.records
            ),
        )
    )

    second = runtime.scan_once()

    assert second.health is HealthState.HEALTHY
    assert second.batch is not None
    assert stale_code not in {
        candidate.code for candidate in second.batch.candidates
    }
    assert second.stale_excluded_count == 1
    assert second.coverage_ratio == pytest.approx(119 / 120)


def test_runtime_clears_intraday_baselines_when_trade_date_advances(
    tmp_path: Path,
) -> None:
    universe = _universe()
    cache = RuntimeUniverseCache(
        tmp_path / "runtime-universe-v1.json",
        minimum_profile_count=100,
    )
    cache.save(universe)
    next_day = NOW + timedelta(days=1)
    next_result = replace(
        _realtime_result(universe),
        records=tuple(
            {
                **record,
                "vol": 100.0,
                "amount": 1000.0,
                "source_ts": next_day.isoformat(),
                "received_ts": next_day.isoformat(),
            }
            for record in _realtime_result(universe).records
        ),
        provenance=replace(
            _realtime_result(universe).provenance,
            source_ts=next_day,
            received_ts=next_day,
        ),
    )

    class SequenceRealtime:
        def __init__(self) -> None:
            self.results = [_realtime_result(universe), next_result]

        def execute(self, _request: object) -> TransportResult:
            return self.results.pop(0)

    current = [NOW]
    runtime = TushareV1Runtime(
        cast(TushareBootstrapLoader, _ForbiddenLoader()),
        FullMarketScanCoordinator(
            SequenceRealtime(),
            clock=lambda: current[0],
        ),
        universe_cache=cache,
        clock=lambda: current[0],
    )

    first = runtime.scan_once()
    current[0] = next_day
    second = runtime.scan_once()

    assert first.health is HealthState.HEALTHY
    assert second.failure_reason != "sequence"
    assert second.health is HealthState.HEALTHY


def test_missing_cache_stops_safely_without_pro_or_realtime_call(
    tmp_path: Path,
) -> None:
    loader = _ForbiddenLoader()
    realtime = _RecordingRealtime(_realtime_result(_universe()))
    runtime = TushareV1Runtime(
        cast(TushareBootstrapLoader, loader),
        FullMarketScanCoordinator(realtime, clock=lambda: NOW),
        universe_cache=RuntimeUniverseCache(
            tmp_path / "missing.json",
            minimum_profile_count=100,
        ),
        clock=lambda: NOW,
    )

    outcome = runtime.scan_once()

    assert outcome.health is HealthState.STOPPED
    assert outcome.failure_reason == "universe_cache"
    assert loader.calls == 0
    assert realtime.calls == 0


def test_concept_cache_keeps_last_known_good_after_failed_refresh(
    tmp_path: Path,
) -> None:
    """A failed concept refresh must never erase verified concept memberships."""
    cache = RuntimeUniverseCache(
        tmp_path / "runtime-universe-v1.json",
        minimum_profile_count=100,
    )
    base = _universe()
    concept_membership = SectorMembership(
        security=_security(1),
        sector_code="C1",
        sector_name="概念1",
        sector_type="concept",
        member_count=5,
        effective_date=NOW.date(),
        source_ts=NOW,
        received_ts=NOW,
        provider_version="pro-cache-test",
        config_version="runtime-universe-v1",
        quality=DataQuality.DEGRADED,
        source_timestamp_kind=SourceTimestampKind.RECEIVED_FALLBACK,
    )
    concept_universe = replace(
        base,
        concept_loaded=True,
        memberships=(*base.memberships, concept_membership),
    )
    cache.save(concept_universe)
    assert cache.load(now=NOW).concept_loaded

    industry_only = replace(_universe(), concept_loaded=False)
    preserved = cache.save_preserving_last_known_good(industry_only, concept_universe)
    assert preserved is True
    reloaded = cache.load(now=NOW)
    assert reloaded.concept_loaded is True

    assert cache.save_preserving_last_known_good(concept_universe, industry_only) is False
    assert cache.load(now=NOW).concept_loaded is True

    cold = cache.save_preserving_last_known_good(industry_only, None)
    assert cold is False
    assert cache.load(now=NOW).concept_loaded is False
