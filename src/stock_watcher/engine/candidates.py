from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime

from stock_watcher.domain import (
    SHANGHAI,
    CandidateInput,
    FundPriceSyncState,
    FundSignalState,
    HealthState,
)


@dataclass(frozen=True, slots=True)
class CandidateConfig:
    version: str
    app_version: str
    strong_change_pct: float = 5.0
    strong_velocity_pct: float = 2.0
    sector_pass_strength: float = 2.0
    medium_score: float = 7.0
    strong_core_score: float = 50.0
    medium_core_score: float = 32.0
    maximum_same_sector: int = 2
    display_count: int = 3


@dataclass(frozen=True, slots=True)
class Candidate:
    code: str
    name: str
    sector: str
    level: str
    score: float
    price_score: float
    sector_score: float
    trend_score: float
    penalty: float
    reasons: tuple[str, ...]
    source_ts: datetime
    provider_version: str
    config_version: str
    app_version: str
    price: float = 0.0
    change_pct: float = 0.0
    velocity_pct: float = 0.0
    sector_code: str = ""
    sector_type: str = "industry"
    is_formal: bool = False
    is_supplement: bool = True
    core_score: float = 0.0
    fund_score: float = 0.0
    total_score: float = 0.0
    data_completeness: float = 0.0
    super_large_state: str = FundSignalState.UNCONFIRMED.value
    large_state: str = FundSignalState.UNCONFIRMED.value
    fund_sync_state: str = FundPriceSyncState.UNCONFIRMED.value
    fund_label: str = "资金未确认"
    trend_label: str = "一般"
    acceleration_pct: float | None = None
    velocity_available: bool = False
    sector_gate_passed: bool = False
    sector_up_ratio: float | None = None
    sector_strong_count: int | None = None
    sector_rank_percentile: float | None = None
    sector_median_change_pct: float | None = None
    sector_rank: int | None = None
    sector_valid_count: int | None = None


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    source_ts: datetime
    generated_at: datetime
    candidates: tuple[Candidate, ...]
    health: HealthState
    overall_weak: bool
    fund_module: str = "unavailable"
    formal_count: int = 0

    def trace_payload(self) -> str:
        """Canonical JSON retained with every snapshot for replay and audit."""
        return json.dumps(asdict(self), default=str, sort_keys=True, separators=(",", ":"))


class CandidateEngine:
    """Pure V1 scoring and fixed-three selection with an explicit supplement tier."""

    def calculate(
        self,
        inputs: tuple[CandidateInput, ...],
        health: HealthState,
        config: CandidateConfig,
    ) -> CandidateBatch | None:
        if health is not HealthState.HEALTHY:
            return None
        if not inputs:
            return CandidateBatch(
                datetime.min.replace(tzinfo=SHANGHAI),
                datetime.min.replace(tzinfo=SHANGHAI),
                (),
                health,
                True,
            )
        eligible = [item for item in inputs if item.exclusion_reason is None]
        evaluated = [self._evaluate(item, config) for item in eligible]
        evaluated.sort(
            key=lambda candidate: (
                not candidate.is_formal,
                -candidate.total_score,
                -candidate.core_score,
                candidate.code,
            )
        )
        selected = self._diversify_and_fill(evaluated, config)
        source_ts = max(item.source_ts for item in inputs)
        generated_at = max(item.received_ts for item in inputs)
        formal_count = sum(candidate.is_formal for candidate in selected)
        fund_available = any(
            candidate.fund_label != "资金未确认" for candidate in selected
        )
        return CandidateBatch(
            source_ts=source_ts,
            generated_at=generated_at,
            candidates=tuple(selected),
            health=health,
            overall_weak=formal_count < config.display_count,
            fund_module="available" if fund_available else "unavailable",
            formal_count=formal_count,
        )

    def evaluate_input(
        self,
        item: CandidateInput,
        config: CandidateConfig,
    ) -> Candidate | None:
        """Refresh one currently displayed row from this scan's eligible input."""
        if item.exclusion_reason is not None:
            return None
        return self._evaluate(item, config)

    def refresh_stable_candidates(
        self,
        inputs: tuple[CandidateInput, ...],
        codes: tuple[str, ...],
        config: CandidateConfig,
    ) -> dict[str, Candidate]:
        """Refresh retained positions without weakening eligibility or diversity."""
        inputs_by_code = {item.security.code: item for item in inputs}
        refreshed: list[Candidate] = []
        for code in codes:
            item = inputs_by_code.get(code)
            if item is None:
                continue
            candidate = self.evaluate_input(item, config)
            if candidate is not None:
                refreshed.append(candidate)
        normalized = self._enforce_sector_limit(refreshed, config)
        return {candidate.code: candidate for candidate in normalized}

    def _diversify_and_fill(
        self,
        evaluated: list[Candidate],
        config: CandidateConfig,
    ) -> list[Candidate]:
        selected: list[Candidate] = []
        sector_counts: dict[str, int] = {}
        enforce_sector_limit = any(candidate.sector_code for candidate in evaluated)
        for candidate in evaluated:
            if not candidate.is_formal:
                continue
            sector_key = candidate.sector_code or candidate.sector
            if (
                enforce_sector_limit
                and sector_counts.get(sector_key, 0) >= config.maximum_same_sector
            ):
                continue
            selected.append(candidate)
            sector_counts[sector_key] = sector_counts.get(sector_key, 0) + 1
            if len(selected) == config.display_count:
                return selected
        for candidate in evaluated:
            if candidate in selected:
                continue
            sector_key = candidate.sector_code or candidate.sector
            if (
                enforce_sector_limit
                and sector_counts.get(sector_key, 0) >= config.maximum_same_sector
            ):
                continue
            supplement = candidate
            if candidate.is_formal:
                supplement = replace(
                    candidate,
                    level="近",
                    is_formal=False,
                    is_supplement=True,
                    reasons=candidate.reasons + ("同板块名额已满，作为补位观察",),
                )
            selected.append(supplement)
            sector_counts[sector_key] = sector_counts.get(sector_key, 0) + 1
            if len(selected) == config.display_count:
                return selected
        return selected

    @staticmethod
    def _enforce_sector_limit(
        candidates: list[Candidate],
        config: CandidateConfig,
    ) -> tuple[Candidate, ...]:
        """Keep a retained three-row set honest after refreshing its live fields."""
        enforce = any(candidate.sector_code for candidate in candidates)
        sector_counts: dict[str, int] = {}
        output: list[Candidate] = []
        for candidate in candidates:
            sector_key = candidate.sector_code or candidate.sector
            if (
                enforce
                and sector_counts.get(sector_key, 0) >= config.maximum_same_sector
            ):
                continue
            output.append(candidate)
            sector_counts[sector_key] = sector_counts.get(sector_key, 0) + 1
        return tuple(output)

    def _evaluate(self, item: CandidateInput, config: CandidateConfig) -> Candidate:
        velocities = tuple(
            value
            for value in (
                item.velocity_1m_pct,
                item.velocity_3m_pct,
                item.velocity_5m_pct,
            )
            if value is not None
        )
        display_velocity = item.velocity_1m_pct
        if display_velocity is None:
            display_velocity = item.velocity_pct
        sector_passed = (
            item.sector_gate_passed
            if item.sector_gate_passed is not None
            else item.sector_strength >= config.sector_pass_strength
        )
        individual_passed = item.change_pct > 0 and (
            any(value > 0 for value in velocities) if velocities else item.velocity_pct > 0
        )
        is_formal = sector_passed and individual_passed

        sector_score = self._sector_score(item)
        price_score = self._momentum_score(item, velocities)
        trend_score = self._trend_score(item)
        penalty = 0.0 if item.trend_3d_pct >= 0 else min(5.0, abs(item.trend_3d_pct))
        core_score = max(0.0, sector_score + price_score + trend_score - penalty)
        fund_score = self._fund_score(item)
        total_score = min(100.0, core_score + fund_score)

        reasons = self._reasons(
            item,
            sector_passed=sector_passed,
            individual_passed=individual_passed,
            penalty=penalty,
        )
        if not is_formal:
            level = "近"
        elif (
            core_score >= config.strong_core_score
            and item.data_completeness >= 0.75
            and (
                item.acceleration_pct is None
                or item.acceleration_pct >= -0.15
            )
        ):
            level = "强"
        else:
            level = "中"

        fund = item.fund_status
        return Candidate(
            code=item.security.code,
            name=item.security.name,
            sector=item.sector,
            level=level,
            score=round(total_score, 4),
            price_score=round(price_score, 4),
            sector_score=round(sector_score, 4),
            trend_score=round(trend_score, 4),
            penalty=round(penalty, 4),
            reasons=reasons,
            source_ts=item.source_ts,
            provider_version=item.provider_version,
            config_version=config.version,
            app_version=config.app_version,
            price=item.price,
            change_pct=item.change_pct,
            velocity_pct=display_velocity,
            sector_code=item.sector_code,
            sector_type=item.sector_type,
            is_formal=is_formal,
            is_supplement=not is_formal,
            core_score=round(core_score, 4),
            fund_score=round(fund_score, 4),
            total_score=round(total_score, 4),
            data_completeness=item.data_completeness,
            super_large_state=fund.super_large_state.value,
            large_state=fund.large_state.value,
            fund_sync_state=fund.price_sync_state.value,
            fund_label=self._fund_label(item),
            trend_label=self._trend_label(item),
            acceleration_pct=item.acceleration_pct,
            velocity_available=item.velocity_1m_pct is not None,
            sector_gate_passed=sector_passed,
            sector_up_ratio=item.sector_up_ratio,
            sector_strong_count=item.sector_strong_count,
            sector_rank_percentile=item.sector_rank_percentile,
            sector_median_change_pct=item.sector_median_change_pct,
            sector_rank=item.sector_rank,
            sector_valid_count=item.sector_valid_count,
        )

    @staticmethod
    def _sector_score(item: CandidateInput) -> float:
        if item.sector_gate_passed is None:
            return min(30.0, max(0.0, item.sector_strength * 10.0))
        score = min(30.0, max(0.0, item.sector_strength))
        if item.sector_rank_percentile is not None:
            score = min(30.0, score + max(0.0, 1.0 - item.sector_rank_percentile) * 3.0)
        return score

    @staticmethod
    def _momentum_score(
        item: CandidateInput,
        velocities: tuple[float, ...],
    ) -> float:
        change_score = min(10.0, max(0.0, item.change_pct) / 7.0 * 10.0)
        velocity_values = velocities or (item.velocity_pct,)
        velocity_score = min(
            10.0,
            sum(max(0.0, value) for value in velocity_values)
            / len(velocity_values)
            / 2.0
            * 10.0,
        )
        acceleration_score = (
            min(3.0, max(0.0, item.acceleration_pct) * 3.0)
            if item.acceleration_pct is not None
            else 0.0
        )
        volume_score = 0.0
        for ratio in (item.volume_ratio_1m, item.amount_ratio_1m):
            if ratio is not None and ratio > 1:
                volume_score += min(2.0, ratio - 1.0)
        breakout_score = (
            (1.5 if item.intraday_high_break else 0.0)
            + (1.5 if item.high_3d_break else 0.0)
        )
        relative_strength = (
            item.sector_relative_strength
            if item.sector_relative_strength is not None
            else item.market_relative_strength
        )
        relative_score = (
            min(3.0, max(0.0, relative_strength))
            if relative_strength is not None
            else 0.0
        )
        return min(
            30.0,
            change_score
            + velocity_score
            + acceleration_score
            + volume_score
            + breakout_score
            + relative_score,
        )

    @staticmethod
    def _trend_score(item: CandidateInput) -> float:
        score = min(6.0, max(0.0, item.trend_3d_pct) * 2.0 + 2.0)
        score += 1.5 if item.highs_rising_3d else 0.0
        score += 1.5 if item.lows_rising_3d else 0.0
        score += 1.0 if item.amount_rising_3d else 0.0
        return min(10.0, score)

    @staticmethod
    def _fund_score(item: CandidateInput) -> float:
        fund = item.fund_status
        super_score = {
            FundSignalState.ENHANCING: 15.0,
            FundSignalState.NEUTRAL: 5.0,
            FundSignalState.WEAK: 0.0,
            FundSignalState.UNCONFIRMED: 0.0,
        }[fund.super_large_state]
        large_score = {
            FundSignalState.ENHANCING: 10.0,
            FundSignalState.NEUTRAL: 3.0,
            FundSignalState.WEAK: 0.0,
            FundSignalState.UNCONFIRMED: 0.0,
        }[fund.large_state]
        sync_score = (
            5.0 if fund.price_sync_state is FundPriceSyncState.SYNCHRONIZED else 0.0
        )
        return super_score + large_score + sync_score

    @staticmethod
    def _fund_label(item: CandidateInput) -> str:
        fund = item.fund_status
        if (
            fund.super_large_state is FundSignalState.UNCONFIRMED
            and fund.large_state is FundSignalState.UNCONFIRMED
        ):
            return "资金未确认"
        labels = {
            FundSignalState.ENHANCING: "增强",
            FundSignalState.NEUTRAL: "一般",
            FundSignalState.WEAK: "走弱",
            FundSignalState.UNCONFIRMED: "未确认",
        }
        return (
            f"超大单{labels[fund.super_large_state]}｜"
            f"大单{labels[fund.large_state]}"
        )

    @staticmethod
    def _trend_label(item: CandidateInput) -> str:
        if item.highs_rising_3d and item.lows_rising_3d:
            return "向上"
        if item.trend_3d_pct > 0:
            return "转强"
        return "一般"

    @staticmethod
    def _reasons(
        item: CandidateInput,
        *,
        sector_passed: bool,
        individual_passed: bool,
        penalty: float,
    ) -> tuple[str, ...]:
        reasons = [
            f"当前涨幅 {item.change_pct:.2f}%",
            (
                f"1/3/5分钟涨速 "
                f"{_optional_pct(item.velocity_1m_pct)}/"
                f"{_optional_pct(item.velocity_3m_pct)}/"
                f"{_optional_pct(item.velocity_5m_pct)}"
            ),
            f"{item.sector}板块{'通过强势门槛' if sector_passed else '尚未通过强势门槛'}",
        ]
        if item.amount_ratio_1m is not None and item.amount_ratio_1m > 1:
            reasons.append(f"近1分钟成交额放大至常态的 {item.amount_ratio_1m:.1f} 倍")
        if item.intraday_high_break:
            reasons.append("价格突破当日前高")
        if item.high_3d_break:
            reasons.append("价格创最近三个交易日新高")
        if (
            item.sector_relative_strength is not None
            and item.sector_relative_strength > 0
        ):
            reasons.append(
                f"相对板块强 {item.sector_relative_strength:.2f} 个百分点"
            )
        if item.trend_3d_pct != 0:
            reasons.append(f"最近三个交易日累计 {item.trend_3d_pct:+.2f}%")
        if not sector_passed:
            reasons.append("板块未通过，最高仅近")
        elif not individual_passed:
            reasons.append("个股动量不足，作为补位观察")
        if item.fund_status.super_large_state is FundSignalState.UNCONFIRMED:
            reasons.append("本轮资金未确认，不计入资金评分")
        if penalty:
            reasons.append(f"三日趋势回落，扣减 {penalty:.2f}")
        return tuple(reasons[:7])


def _optional_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}%"
