from __future__ import annotations

import json
from collections import Counter
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
    volume_ratio_1m: float | None = None
    amount_ratio_1m: float | None = None
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


@dataclass(frozen=True, slots=True)
class CandidateAuditRow:
    raw_rank: int
    code: str
    name: str
    sector: str
    sector_code: str
    sector_type: str
    total_score: float
    core_score: float
    level: str
    is_formal: bool
    velocity_available: bool
    selected_raw: bool
    selected_stable: bool
    decision: str


@dataclass(frozen=True, slots=True)
class CandidateSelectionAudit:
    warmup_state: str
    input_count: int
    eligible_count: int
    formal_count: int
    velocity_1m_ready_count: int
    velocity_3m_ready_count: int
    velocity_5m_ready_count: int
    raw_codes: tuple[str, ...]
    stable_codes: tuple[str, ...]
    excluded_counts: dict[str, int]
    rows: tuple[CandidateAuditRow, ...]

    @property
    def display_velocity_ready(self) -> bool:
        displayed = [row for row in self.rows if row.selected_stable]
        return bool(displayed) and all(row.velocity_available for row in displayed)

    def trace_payload(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class CandidateEngine:
    """Pure V1 scoring and fixed-three selection with an explicit supplement tier."""

    def rank_formal_candidates(
        self,
        inputs: tuple[CandidateInput, ...],
        config: CandidateConfig,
    ) -> tuple[Candidate, ...]:
        """Return the current formal candidate pool in deterministic score order.

        The displayed batch is intentionally diversified and capped at three rows,
        while anomaly detection needs to see the broader set of stocks that already
        passed the individual and sector gates.  Keeping this ranking in the engine
        makes both paths use the exact same scoring and exclusion rules.
        """
        evaluated = list(self.rank_all_candidates(inputs, config))
        return tuple(candidate for candidate in evaluated if candidate.is_formal)

    def rank_all_candidates(
        self,
        inputs: tuple[CandidateInput, ...],
        config: CandidateConfig,
    ) -> tuple[Candidate, ...]:
        """Return every eligible stock in the same order used by Top3 selection."""
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
        return tuple(evaluated)

    def build_selection_audit(
        self,
        inputs: tuple[CandidateInput, ...],
        raw: CandidateBatch | None,
        stable: CandidateBatch | None,
        config: CandidateConfig,
        *,
        limit: int = 20,
    ) -> CandidateSelectionAudit:
        """Explain why the visible Top3 differs from the current score order.

        This deliberately records the pre-stability ranking as well as the
        visible list.  It is the evidence needed to distinguish real market
        movement from warm-up, sector diversification and debounce holds.
        """
        evaluated = self.rank_all_candidates(inputs, config)
        raw_codes = tuple(candidate.code for candidate in raw.candidates) if raw else ()
        stable_codes = (
            tuple(candidate.code for candidate in stable.candidates) if stable else ()
        )
        selection_stage = self._selection_stage(evaluated, config)
        ranks = {candidate.code: rank for rank, candidate in enumerate(evaluated, start=1)}
        audit_candidates = list(evaluated[:limit])
        included_codes = {candidate.code for candidate in audit_candidates}
        for code in (*raw_codes, *stable_codes):
            if code in included_codes:
                continue
            retained = next(
                (candidate for candidate in evaluated if candidate.code == code),
                None,
            )
            if retained is not None:
                audit_candidates.append(retained)
                included_codes.add(code)
        rows: list[CandidateAuditRow] = []
        for candidate in audit_candidates:
            rank = ranks[candidate.code]
            if candidate.code in stable_codes and candidate.code in raw_codes:
                decision = "displayed"
            elif candidate.code in stable_codes:
                decision = "retained_by_stability"
            elif candidate.code in raw_codes:
                decision = "raw_top3_blocked_by_stability"
            else:
                decision = selection_stage.get(candidate.code, "below_cutoff")
            rows.append(
                CandidateAuditRow(
                    raw_rank=rank,
                    code=candidate.code,
                    name=candidate.name,
                    sector=candidate.sector,
                    sector_code=candidate.sector_code,
                    sector_type=candidate.sector_type,
                    total_score=candidate.total_score,
                    core_score=candidate.core_score,
                    level=candidate.level,
                    is_formal=candidate.is_formal,
                    velocity_available=candidate.velocity_available,
                    selected_raw=candidate.code in raw_codes,
                    selected_stable=candidate.code in stable_codes,
                    decision=decision,
                )
            )
        eligible_inputs = tuple(item for item in inputs if item.exclusion_reason is None)
        ready_1m = sum(item.velocity_1m_pct is not None for item in eligible_inputs)
        ready_3m = sum(item.velocity_3m_pct is not None for item in eligible_inputs)
        ready_5m = sum(item.velocity_5m_pct is not None for item in eligible_inputs)
        if not eligible_inputs or ready_1m == 0:
            warmup_state = "cold"
        elif ready_1m / len(eligible_inputs) < 0.8:
            warmup_state = "warming"
        else:
            warmup_state = "ready"
        exclusions = Counter(
            reason
            for item in inputs
            if (reason := item.exclusion_reason) is not None
        )
        return CandidateSelectionAudit(
            warmup_state=warmup_state,
            input_count=len(inputs),
            eligible_count=len(eligible_inputs),
            formal_count=sum(candidate.is_formal for candidate in evaluated),
            velocity_1m_ready_count=ready_1m,
            velocity_3m_ready_count=ready_3m,
            velocity_5m_ready_count=ready_5m,
            raw_codes=raw_codes,
            stable_codes=stable_codes,
            excluded_counts=dict(sorted(exclusions.items())),
            rows=tuple(rows),
        )

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
    def _selection_stage(
        evaluated: tuple[Candidate, ...],
        config: CandidateConfig,
    ) -> dict[str, str]:
        """Reproduce the raw selection stage and label the first exclusion reason."""
        decisions: dict[str, str] = {}
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
                decisions[candidate.code] = "same_sector_limit"
                continue
            if len(selected) >= config.display_count:
                decisions[candidate.code] = "below_formal_top3"
                continue
            selected.append(candidate)
            sector_counts[sector_key] = sector_counts.get(sector_key, 0) + 1
            decisions[candidate.code] = "raw_selected"
        for candidate in evaluated:
            if candidate in selected:
                continue
            if len(selected) >= config.display_count:
                decisions.setdefault(
                    candidate.code,
                    "supplement_below_cutoff" if not candidate.is_formal else "below_formal_top3",
                )
                continue
            sector_key = candidate.sector_code or candidate.sector
            if (
                enforce_sector_limit
                and sector_counts.get(sector_key, 0) >= config.maximum_same_sector
            ):
                decisions.setdefault(candidate.code, "same_sector_limit")
                continue
            selected.append(candidate)
            sector_counts[sector_key] = sector_counts.get(sector_key, 0) + 1
            decisions[candidate.code] = "raw_selected"
        return decisions

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
        elif core_score >= config.medium_core_score:
            level = "中"
        else:
            level = "近"

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
            volume_ratio_1m=item.volume_ratio_1m,
            amount_ratio_1m=item.amount_ratio_1m,
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
