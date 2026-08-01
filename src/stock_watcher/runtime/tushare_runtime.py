from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from threading import RLock
from typing import Protocol

from stock_watcher.domain import (
    SHANGHAI,
    DataQuality,
    HealthState,
    RealtimeQuote,
    SectorMembership,
    Security,
    SourceTimestampKind,
)
from stock_watcher.engine import (
    CandidateBatch,
    CandidateConfig,
    CandidateEngine,
    CandidatePipeline,
    FundCapability,
    FundCapabilityResult,
    FundEngine,
    MarketSnapshotBuffer,
    SecurityProfile,
    SnapshotSequenceError,
    StableTop3Selector,
    StrongMovementDetector,
    StrongMovementEvent,
    ThreeDayTrend,
)
from stock_watcher.providers.tushare.errors import (
    ProviderError,
    ProviderFailureReason,
)
from stock_watcher.providers.tushare.models import TransportResult

from .data_health import DataHealthTracker
from .scan_coordinator import (
    FullMarketScanCoordinator,
    IncompleteScanError,
    ScanCancelledError,
    ScanInProgressError,
)
from .universe_cache import (
    RuntimeUniverseCache,
    UniverseCacheError,
    UniverseCacheFailure,
    universe_is_current,
)


class BootstrapProvider(Protocol):
    def stock_list(self, **params: str | int | float | bool) -> TransportResult: ...

    def trading_dates(self, **params: str | int | float | bool) -> TransportResult: ...

    def daily_bars(self, **params: str | int | float | bool) -> TransportResult: ...

    def historical_minutes(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult: ...

    def sector_classification(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult: ...

    def sector_components(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult: ...

    def concept_classification(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult: ...

    def concept_components(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult: ...

    def adjustment_factors(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult: ...

    def suspension_events(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult: ...

    def moneyflow(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult: ...


@dataclass(frozen=True, slots=True)
class RuntimeUniverse:
    profiles: tuple[SecurityProfile, ...]
    memberships: tuple[SectorMembership, ...]
    trends: dict[str, ThreeDayTrend]
    high_3d: dict[str, float]
    open_dates: tuple[date, ...]
    concept_loaded: bool
    fund_capability: FundCapabilityResult = FundCapabilityResult(
        FundCapability.UNAVAILABLE,
        "尚未探测资金能力",
    )
    generated_at: datetime | None = None
    trend_through_date: date | None = None

    @property
    def securities(self) -> tuple[Security, ...]:
        return tuple(profile.security for profile in self.profiles)


@dataclass(frozen=True, slots=True)
class ScanOutcome:
    health: HealthState
    detail: str
    batch: CandidateBatch | None
    raw_batch: CandidateBatch | None
    strong_event: StrongMovementEvent | None
    elapsed_seconds: float | None
    coverage_ratio: float | None
    source_age_seconds: float | None = None
    source_span_seconds: float | None = None
    failure_reason: str | None = None
    stale_excluded_count: int = 0
    unavailable_excluded_count: int = 0


class TushareBootstrapLoader:
    """Loads daily universe/sector/trend context using batched Pro requests."""

    completed_session_count = 4
    maximum_calendar_lookback_days = 20

    def __init__(
        self,
        provider: BootstrapProvider,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.provider = provider
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self._stock_result: TransportResult | None = None
        self._daily_results: dict[date, TransportResult] = {}
        self._empty_daily_dates: set[date] = set()

    def load(self) -> RuntimeUniverse:
        now = _shanghai(self._clock())
        stock_result = self._stock_result
        if stock_result is None:
            stock_result = self.provider.stock_list(
                exchange="",
                list_status="L",
            )
            self._stock_result = stock_result
        if not stock_result.records:
            raise RuntimeError("证券列表为空")
        include_today = now.timetz().replace(tzinfo=None) >= time(15, 30)
        latest_dates, daily_results = self._recent_daily_snapshots(
            now.date() if include_today else now.date() - timedelta(days=1)
        )
        daily_records = [
            record
            for trading_date in latest_dates
            for record in daily_results[trading_date].records
        ]
        open_dates = latest_dates
        if not include_today and now.date().weekday() < 5:
            # trade_cal is a separately throttled capability and must not block
            # an intraday manual Top3 request. A weekday is provisionally
            # admitted here; the native realtime freshness gate remains the
            # authoritative fail-closed check on exchange holidays.
            open_dates = tuple(sorted((*open_dates, now.date())))
        trends, high_3d = _daily_context(tuple(daily_records))
        profiles = _security_profiles(
            stock_result,
            open_dates,
            _missing_latest_daily_codes(
                tuple(daily_records),
                latest_date=latest_dates[-1],
            ),
        )
        if len(profiles) < 100:
            raise RuntimeError("证券列表覆盖不足")
        profile_codes = {profile.security.code for profile in profiles}
        trends = {
            code: trend
            for code, trend in trends.items()
            if code in profile_codes
        }
        high_3d = {
            code: highest
            for code, highest in high_3d.items()
            if code in profile_codes
        }
        industry_memberships = _stock_basic_industry_memberships(
            profiles,
            stock_result,
            observed_at=now,
        )
        if len({membership.security.code for membership in industry_memberships}) < 100:
            raise RuntimeError("行业成分覆盖不足")
        # Concept membership is an optional, separately cached capability.  A
        # temporary 429 or an older provider that does not expose tdx_* must not
        # discard the verified industry context, but a complete concept response
        # is merged here so the realtime SectorEngine can apply the locked
        # industry-or-concept gate without another per-round request.
        concept_memberships = self._concept_memberships(profiles, now)
        memberships = (*industry_memberships, *concept_memberships)
        return RuntimeUniverse(
            profiles=profiles,
            memberships=memberships,
            trends=trends,
            high_3d=high_3d,
            open_dates=open_dates,
            concept_loaded=bool(concept_memberships),
            fund_capability=FundCapabilityResult(
                FundCapability.UNAVAILABLE,
                "资金未确认；不阻塞候选",
            ),
            generated_at=now,
            trend_through_date=latest_dates[-1],
        )

    def _recent_daily_snapshots(
        self,
        last_completed_date: date,
    ) -> tuple[tuple[date, ...], dict[date, TransportResult]]:
        """Find four completed sessions using only the verified daily route.

        ``trade_cal`` can be rate-limited independently of the full-market
        ``daily`` endpoint. Empty weekday responses are treated as market
        holidays and skipped, while auth, network and rate-limit failures still
        fail closed with their original reason.
        """

        daily_results: dict[date, TransportResult] = {}
        for offset in range(self.maximum_calendar_lookback_days):
            trading_date = last_completed_date - timedelta(days=offset)
            if trading_date.weekday() >= 5:
                continue
            if trading_date in self._empty_daily_dates:
                continue
            result = self._daily_results.get(trading_date)
            if result is not None:
                daily_results[trading_date] = result
                if len(daily_results) == self.completed_session_count:
                    break
                continue
            try:
                result = self.provider.daily_bars(
                    trade_date=trading_date.strftime("%Y%m%d")
                )
            except ProviderError as error:
                if error.reason is ProviderFailureReason.EMPTY_DATA:
                    self._empty_daily_dates.add(trading_date)
                    continue
                raise
            if not result.records:
                self._empty_daily_dates.add(trading_date)
                continue
            self._daily_results[trading_date] = result
            daily_results[trading_date] = result
            if len(daily_results) == self.completed_session_count:
                break
        if len(daily_results) < self.completed_session_count:
            raise RuntimeError("已完成日线不足4个交易日，保留上一版基础缓存")
        latest_dates = tuple(sorted(daily_results))
        return latest_dates, daily_results

    def warmup_minutes(
        self,
        quotes: tuple[RealtimeQuote, ...],
        *,
        limit: int = 12,
    ) -> tuple[RealtimeQuote, ...]:
        """Preheat a bounded strongest-name set with real 1-minute prices.

        The verified ``stk_mins`` contract accepts one security per request.
        Requests remain sequential behind the provider's one-second start
        limiter; the small bound prevents startup preheating from turning into
        an unbounded per-stock loop.
        """
        if not quotes:
            return ()
        ranked = sorted(
            quotes,
            key=lambda quote: (
                -_change_pct(quote.price, quote.previous_close),
                -quote.amount_cny,
                quote.security.code,
            ),
        )[:limit]
        current_by_code = {quote.security.code: quote for quote in ranked}
        newest = max(quote.source_ts for quote in ranked)
        records: list[dict[str, str | int | float | bool | None]] = []
        for quote in ranked:
            try:
                result = self.provider.historical_minutes(
                    ts_code=quote.security.code,
                    freq="1min",
                    start_date=f"{newest.date().isoformat()} 09:30:00",
                    end_date=newest.strftime("%Y-%m-%d %H:%M:%S"),
                )
            except Exception:
                continue
            records.extend(result.records)
        grouped: dict[str, list[dict[str, str | int | float | bool | None]]] = {}
        for record in records:
            code = _text(record.get("ts_code"))
            source_ts = _record_timestamp(record)
            if (
                code in current_by_code
                and source_ts is not None
                and source_ts.date() == newest.date()
                and source_ts < current_by_code[code].source_ts
            ):
                grouped.setdefault(code, []).append(record)
        warmed: list[RealtimeQuote] = []
        for code, rows in grouped.items():
            rows.sort(key=lambda record: _record_timestamp(record) or newest)
            current = current_by_code[code]
            keep_from = max(0, len(rows) - 15)
            for index, record in enumerate(rows):
                source_ts = _record_timestamp(record)
                if source_ts is None:
                    continue
                if index < keep_from:
                    continue
                close = _float(record.get("close"))
                open_price = _float(record.get("open")) or close
                high = _float(record.get("high")) or close
                low = _float(record.get("low")) or close
                if close <= 0:
                    continue
                warmed.append(
                    RealtimeQuote(
                        security=current.security,
                        price=close,
                        previous_close=current.previous_close,
                        open=open_price,
                        high=high,
                        low=low,
                        # Historical-minute units must be independently verified
                        # before they can share one cumulative series with the
                        # native realtime snapshot.  Price-only priming gives
                        # deterministic 1/3/5-minute returns without creating a
                        # false volume/amount spike at startup.
                        volume_shares=0.0,
                        amount_cny=0.0,
                        source_ts=source_ts,
                        received_ts=current.received_ts,
                        scan_id="historical-minute-warmup",
                        provider_version=current.provider_version,
                        quality=DataQuality.GOOD,
                    )
                )
        return tuple(
            sorted(warmed, key=lambda quote: (quote.source_ts, quote.security.code))
        )

    def _industry_memberships(
        self,
        profiles: tuple[SecurityProfile, ...],
        now: datetime,
    ) -> tuple[SectorMembership, ...]:
        classification = self.provider.sector_classification(level="L1", src="SW2021")
        sectors = [
            (
                _text(record.get("index_code") or record.get("industry_code")),
                _text(record.get("industry_name") or record.get("name")),
            )
            for record in classification.records
        ]
        sectors = [(code, name) for code, name in sectors if code and name]
        if len(sectors) < 20:
            raise RuntimeError("行业分类覆盖不足")
        by_code = {profile.security.code: profile.security for profile in profiles}
        output: list[SectorMembership] = []
        for sector_code, sector_name in sectors:
            result = self.provider.sector_components(l1_code=sector_code, is_new="Y")
            members = [
                _text(record.get("ts_code") or record.get("con_code"))
                for record in result.records
            ]
            valid = [code for code in members if code in by_code]
            if len(valid) < 3:
                raise RuntimeError("行业成分覆盖不足")
            for code in valid:
                output.append(
                    _membership(
                        by_code[code],
                        sector_code=sector_code,
                        sector_name=sector_name,
                        sector_type="industry",
                        member_count=len(valid),
                        observed_at=now,
                        result=result,
                    )
                )
        return tuple(output)

    def _concept_memberships(
        self,
        profiles: tuple[SecurityProfile, ...],
        now: datetime,
    ) -> tuple[SectorMembership, ...]:
        """Best-effort same-provider concept load; absence never fakes a concept gate."""
        try:
            classification = self.provider.concept_classification(index_type="概念板块")
            bulk = self.provider.concept_components()
        except Exception:
            return ()
        names: dict[str, str] = {}
        expected_counts: dict[str, int] = {}
        for record in classification.records:
            sector_code = _text(
                record.get("ts_code")
                or record.get("index_code")
                or record.get("code")
            )
            name = _text(record.get("name") or record.get("industry_name"))
            if sector_code and name:
                names[sector_code] = name
                expected = int(
                    _float(
                        record.get("idx_count")
                        or record.get("count")
                        or record.get("member_count")
                    )
                )
                if expected > 0:
                    expected_counts[sector_code] = expected
        by_code = {profile.security.code: profile.security for profile in profiles}
        grouped: dict[str, list[str]] = {}
        for record in bulk.records:
            sector_code = _text(
                record.get("ts_code")
                or record.get("index_code")
                or record.get("sector_code")
            )
            member_code = _text(
                record.get("con_code")
                or record.get("member_code")
                or record.get("stock_code")
            )
            if sector_code in names and member_code in by_code:
                grouped.setdefault(sector_code, []).append(member_code)
        output: list[SectorMembership] = []
        for sector_code, members in grouped.items():
            unique = tuple(dict.fromkeys(members))
            expected_count = expected_counts.get(sector_code)
            if expected_count is None or len(unique) / expected_count < 0.99:
                continue
            for code in unique:
                output.append(
                    _membership(
                        by_code[code],
                        sector_code=sector_code,
                        sector_name=names[sector_code],
                        sector_type="concept",
                        member_count=expected_count,
                        observed_at=now,
                        result=bulk,
                    )
                )
        return tuple(output)

    def _corporate_action_codes(self, open_dates: tuple[date, ...]) -> set[str]:
        if len(open_dates) < 2:
            return set()
        try:
            previous = self.provider.adjustment_factors(
                trade_date=open_dates[-2].strftime("%Y%m%d")
            )
            current = self.provider.adjustment_factors(
                trade_date=open_dates[-1].strftime("%Y%m%d")
            )
        except Exception:
            return set()
        previous_values = {
            _text(record.get("ts_code")): _float(record.get("adj_factor"))
            for record in previous.records
        }
        return {
            code
            for record in current.records
            if (code := _text(record.get("ts_code")))
            and code in previous_values
            and _float(record.get("adj_factor")) != previous_values[code]
        }

    def _resumption_codes(self, current_date: date) -> set[str]:
        try:
            result = self.provider.suspension_events(
                trade_date=current_date.strftime("%Y%m%d"),
                suspend_type="R",
            )
        except Exception:
            return set()
        return {
            code
            for record in result.records
            if (code := _text(record.get("ts_code")))
        }

    def _fund_capability(
        self,
        latest_dates: tuple[date, ...],
    ) -> FundCapabilityResult:
        engine = FundEngine()
        for trading_date in reversed(latest_dates[-3:]):
            try:
                result = self.provider.moneyflow(
                    trade_date=trading_date.strftime("%Y%m%d")
                )
            except Exception:
                continue
            if result.records:
                return engine.probe(result.records)
        return engine.probe(())


class TushareV1Runtime:
    """Stateful real-market pipeline shared by the Windows UI and live M0 tool."""

    def __init__(
        self,
        loader: TushareBootstrapLoader,
        coordinator: FullMarketScanCoordinator,
        *,
        health: DataHealthTracker | None = None,
        buffer: MarketSnapshotBuffer | None = None,
        pipeline: CandidatePipeline | None = None,
        candidate_engine: CandidateEngine | None = None,
        stable_selector: StableTop3Selector | None = None,
        movement_detector: StrongMovementDetector | None = None,
        candidate_config: CandidateConfig | None = None,
        universe_cache: RuntimeUniverseCache | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.loader = loader
        self.coordinator = coordinator
        self.health = health or DataHealthTracker()
        self.buffer = buffer or MarketSnapshotBuffer()
        self.pipeline = pipeline or CandidatePipeline()
        self.candidate_engine = candidate_engine or CandidateEngine()
        self.stable_selector = stable_selector or StableTop3Selector()
        self.movement_detector = movement_detector or StrongMovementDetector()
        self.candidate_config = candidate_config or CandidateConfig(
            version="v1-real-candidates-20260729",
            app_version="0.4.0a1",
        )
        self.universe_cache = universe_cache
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self._state_lock = RLock()
        self.universe: RuntimeUniverse | None = None
        self.universe_cache_failure: str | None = None
        if self.universe_cache is not None:
            try:
                self.universe = self.universe_cache.load(now=_shanghai(self._clock()))
            except UniverseCacheError as error:
                self.universe_cache_failure = error.reason.value

    def prepare(self) -> RuntimeUniverse:
        """Refresh static context outside the critical realtime scan path.

        The provider work happens before the state swap. A failed refresh therefore
        preserves the previous verified universe and its on-disk cache.
        """
        cold_start = (
            self.universe is None
            and self.universe_cache_failure in {None, UniverseCacheFailure.MISSING.value}
        )
        fresh = self.loader.load()
        if self.universe_cache is not None:
            self.universe_cache.save(fresh)
        with self._state_lock:
            self.buffer.clear()
            self.pipeline.reset()
            self.stable_selector.reset()
            self.movement_detector.reset()
            if cold_start:
                self.health.reset_for_initial()
            else:
                self.health.reset_for_recovery()
            self.universe = fresh
            self.universe_cache_failure = None
        return fresh

    def universe_is_current(self, now: datetime) -> bool:
        with self._state_lock:
            universe = self.universe
        return universe is not None and universe_is_current(universe, now=now)

    def request_scan_cancellation(self) -> None:
        self.coordinator.cancel_current_scan()

    def reset_for_external_recovery(self) -> None:
        """Clear volatile baselines while retaining validated static context."""
        self.request_scan_cancellation()
        with self._state_lock:
            self.buffer.clear()
            self.pipeline.reset()
            self.stable_selector.reset()
            self.movement_detector.reset()
            self.health.reset_for_recovery()

    def scan_once(self) -> ScanOutcome:
        with self._state_lock:
            return self._scan_once_locked()

    def _scan_once_locked(self) -> ScanOutcome:
        universe = self.universe
        if universe is None:
            state = self.health.fail()
            return ScanOutcome(
                state,
                "基础缓存未准备完成，本轮未发起实时请求。",
                None,
                None,
                None,
                None,
                None,
                failure_reason="universe_cache",
            )
        try:
            scan = self.coordinator.fetch_once(universe.securities)
            features = self.buffer.update(
                scan.quotes,
                high_3d=universe.high_3d,
            )
            state = self.health.observe(scan)
        except Exception as error:
            state = self.health.fail()
            return ScanOutcome(
                state,
                "实时或板块核心数据中断，保留上次结果。",
                None,
                None,
                None,
                None,
                None,
                failure_reason=_safe_scan_failure(error),
            )
        if state is not HealthState.HEALTHY:
            return ScanOutcome(
                state,
                (
                    f"实时数据恢复预热 {self.health.fresh_cycles}/"
                    f"{self.health.required_cycles} 轮。"
                ),
                None,
                None,
                None,
                scan.elapsed_seconds,
                scan.coverage_ratio,
                scan.max_source_age_seconds,
                scan.source_span_seconds,
                stale_excluded_count=scan.stale_excluded_count,
                unavailable_excluded_count=scan.unavailable_excluded_count,
            )
        inputs = self.pipeline.build(
            scan.quotes,
            features,
            universe.profiles,
            universe.memberships,
            trends=universe.trends,
            config_version=self.candidate_config.version,
        )
        raw = self.candidate_engine.calculate(
            inputs,
            HealthState.HEALTHY,
            self.candidate_config,
        )
        if raw is None or len(raw.candidates) < 3:
            self.health.fail()
            return ScanOutcome(
                HealthState.STOPPED,
                "合规证券不足3只，本轮不生成新名单。",
                None,
                raw,
                None,
                scan.elapsed_seconds,
                scan.coverage_ratio,
                scan.max_source_age_seconds,
                scan.source_span_seconds,
                failure_reason="candidates",
                stale_excluded_count=scan.stale_excluded_count,
                unavailable_excluded_count=scan.unavailable_excluded_count,
            )
        current_codes = tuple(
            candidate.code for candidate in self.stable_selector.current
        )
        current_candidates = self.candidate_engine.refresh_stable_candidates(
            inputs,
            current_codes,
            self.candidate_config,
        )
        anomaly_pool = self.candidate_engine.rank_formal_candidates(
            inputs,
            self.candidate_config,
        )
        strong_event = self.movement_detector.evaluate(
            raw,
            candidate_pool=anomaly_pool,
        )
        stable = self.stable_selector.update(
            raw,
            current_candidates=current_candidates,
            now=scan.completed_at,
            force=strong_event is not None,
        )
        excluded_count = (
            scan.stale_excluded_count + scan.unavailable_excluded_count
        )
        excluded_detail = (
            f"，排除旧或不可用行情 {excluded_count} 只"
            if excluded_count
            else ""
        )
        return ScanOutcome(
            HealthState.HEALTHY,
            (
                f"全市场覆盖 {scan.coverage_ratio:.1%}，"
                f"本轮 {scan.elapsed_seconds:.1f} 秒，"
                f"行情最旧 {scan.max_source_age_seconds:.1f} 秒"
                f"{excluded_detail}。"
            ),
            stable,
            raw,
            strong_event,
            scan.elapsed_seconds,
            scan.coverage_ratio,
            scan.max_source_age_seconds,
            scan.source_span_seconds,
            stale_excluded_count=scan.stale_excluded_count,
            unavailable_excluded_count=scan.unavailable_excluded_count,
        )


def _security_profiles(
    result: TransportResult,
    open_dates: tuple[date, ...],
    mechanical_jump_codes: set[str],
) -> tuple[SecurityProfile, ...]:
    output: list[SecurityProfile] = []
    for record in result.records:
        code = _text(record.get("ts_code"))
        name = _text(record.get("name"))
        suffix = code.rpartition(".")[2].upper()
        if not code or not name or suffix not in {"SH", "SZ", "BJ"}:
            continue
        list_date = _compact_date(record.get("list_date"))
        listed_days = _listed_trading_days(list_date, open_dates)
        upper = name.upper()
        output.append(
            SecurityProfile(
                security=Security(code=code, name=name, market=suffix),
                listed_trading_days=listed_days,
                is_st=upper.startswith(("ST", "*ST")),
                is_delisting="退" in name,
                is_corporate_action_day=code in mechanical_jump_codes,
            )
        )
    return tuple(sorted(output, key=lambda profile: profile.security.code))


def _missing_latest_daily_codes(
    records: tuple[dict[str, str | int | float | bool | None], ...],
    *,
    latest_date: date,
) -> set[str]:
    """Conservatively exclude codes absent from the latest completed session."""

    all_codes: set[str] = set()
    latest_codes: set[str] = set()
    for record in records:
        code = _text(record.get("ts_code")).upper()
        if not code:
            continue
        all_codes.add(code)
        if _compact_date(record.get("trade_date")) == latest_date:
            latest_codes.add(code)
    return all_codes - latest_codes


def _stock_basic_industry_memberships(
    profiles: tuple[SecurityProfile, ...],
    result: TransportResult,
    *,
    observed_at: datetime,
) -> tuple[SectorMembership, ...]:
    """Create the industry gate directly from one full stock_basic response."""

    securities = {profile.security.code: profile.security for profile in profiles}
    grouped: dict[str, list[str]] = {}
    for record in result.records:
        code = _text(record.get("ts_code")).upper()
        if code not in securities:
            continue
        industry = _text(record.get("industry"))
        if industry:
            grouped.setdefault(industry, []).append(code)
    output: list[SectorMembership] = []
    for industry in sorted(grouped):
        codes = tuple(sorted(set(grouped[industry])))
        if len(codes) < 3:
            continue
        for code in codes:
            output.append(
                _membership(
                    securities[code],
                    sector_code=f"industry:{industry}",
                    sector_name=industry,
                    sector_type="industry",
                    member_count=len(codes),
                    observed_at=observed_at,
                    result=result,
                )
            )
    return tuple(output)


def _safe_scan_failure(error: Exception) -> str:
    if isinstance(error, ProviderError):
        return error.reason.value
    if isinstance(error, ScanInProgressError):
        return "overlap"
    if isinstance(error, ScanCancelledError):
        return "cancelled"
    if isinstance(error, SnapshotSequenceError):
        return "sequence"
    if isinstance(error, IncompleteScanError):
        safe_text = str(error).casefold()
        if "duplicate" in safe_text:
            return "duplicate"
        if "coverage" in safe_text:
            return "coverage"
        if "timestamp" in safe_text or "source" in safe_text:
            return "timestamp"
        return "incomplete"
    return "provider"


def _listed_trading_days(
    list_date: date | None,
    open_dates: tuple[date, ...],
) -> int:
    if list_date is None:
        return 0
    if not open_dates or list_date < open_dates[0]:
        return 999
    return sum(day >= list_date for day in open_dates)


def _daily_context(
    records: tuple[dict[str, str | int | float | bool | None], ...],
) -> tuple[dict[str, ThreeDayTrend], dict[str, float]]:
    grouped: dict[str, list[dict[str, str | int | float | bool | None]]] = {}
    for record in records:
        code = _text(record.get("ts_code"))
        if code:
            grouped.setdefault(code, []).append(record)
    trends: dict[str, ThreeDayTrend] = {}
    highs: dict[str, float] = {}
    for code, rows in grouped.items():
        rows.sort(key=lambda row: _text(row.get("trade_date")))
        recent = rows[-3:]
        if not recent:
            continue
        closes = [_float(row.get("close")) for row in recent]
        high_values = [_float(row.get("high")) for row in recent]
        low_values = [_float(row.get("low")) for row in recent]
        amounts = [_float(row.get("amount")) for row in recent]
        baseline = _float(recent[0].get("pre_close"))
        trends[code] = ThreeDayTrend(
            cumulative_change_pct=(
                (closes[-1] / baseline - 1.0) * 100.0
                if baseline > 0 and closes[-1] > 0
                else 0.0
            ),
            highs_rising=_strictly_rising(high_values),
            lows_rising=_strictly_rising(low_values),
            amount_rising=_strictly_rising(amounts),
            highest_price=max(high_values),
        )
        highs[code] = max(high_values)
    return trends, highs


def _membership(
    security: Security,
    *,
    sector_code: str,
    sector_name: str,
    sector_type: str,
    member_count: int,
    observed_at: datetime,
    result: TransportResult,
) -> SectorMembership:
    received = _shanghai(result.provenance.received_ts)
    source = (
        _shanghai(result.provenance.source_ts)
        if result.provenance.source_ts is not None
        else received
    )
    return SectorMembership(
        security=security,
        sector_code=sector_code,
        sector_name=sector_name,
        sector_type=sector_type,
        member_count=member_count,
        effective_date=observed_at.date(),
        source_ts=source,
        received_ts=received,
        provider_version=result.provenance.provider_version,
        config_version="v1-real-candidates-20260729",
        quality=DataQuality.DEGRADED,
        source_timestamp_kind=SourceTimestampKind.RECEIVED_FALLBACK,
    )


def _compact_date(value: object) -> date | None:
    text = _text(value).replace("-", "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def _truthy(value: object) -> bool:
    return value in (1, "1", True)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _strictly_rising(values: list[float]) -> bool:
    return len(values) >= 2 and all(left < right for left, right in zip(values, values[1:]))


def _shanghai(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)


def _change_pct(price: float, previous_close: float) -> float:
    return (price / previous_close - 1.0) * 100.0 if previous_close > 0 else -999.0


def _record_timestamp(
    record: dict[str, str | int | float | bool | None],
) -> datetime | None:
    value = (
        record.get("source_ts")
        or record.get("trade_time")
        or record.get("datetime")
    )
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _shanghai(parsed)
