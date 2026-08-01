from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median

from stock_watcher.domain import RollingFeatures, SectorMembership, SectorMetrics


@dataclass(frozen=True, slots=True)
class SectorConfig:
    minimum_valid_members: int = 3
    up_ratio_threshold: float = 0.50
    strong_change_pct: float = 2.0
    strong_velocity_1m_pct: float = 0.5
    minimum_strong_count: int = 3
    preferred_up_ratio: float = 2 / 3
    preferred_strong_count: int = 5
    candidate_rank_fraction: float = 0.30
    candidate_rank_cap: int = 5
    industry_tie_margin: float = 1.0
    persistence_bonus_rounds: int = 3
    persistence_bonus: float = 1.0


@dataclass(frozen=True, slots=True)
class SectorSelection:
    metrics: SectorMetrics
    rank: int
    rank_percentile: float
    gate_passed: bool


class SectorEngine:
    """Computes industry/concept breadth and picks each stock's strongest sector."""

    def __init__(self, config: SectorConfig = SectorConfig()) -> None:
        self.config = config
        self._rankings: dict[tuple[str, str], tuple[str, ...]] = {}
        self._gate_streaks: dict[tuple[str, str], int] = {}

    def reset(self) -> None:
        self._rankings = {}
        self._gate_streaks = {}

    def calculate(
        self,
        features: tuple[RollingFeatures, ...],
        memberships: tuple[SectorMembership, ...],
    ) -> dict[tuple[str, str], SectorMetrics]:
        by_code = {feature.code: feature for feature in features}
        grouped: dict[tuple[str, str], list[RollingFeatures]] = {}
        for membership in memberships:
            feature = by_code.get(membership.security.code)
            if feature is None:
                continue
            key = (membership.sector_type.casefold(), membership.sector_code)
            grouped.setdefault(key, []).append(feature)

        metrics: dict[tuple[str, str], SectorMetrics] = {}
        self._rankings = {}
        next_gate_streaks: dict[tuple[str, str], int] = {}
        membership_names = {
            (membership.sector_type.casefold(), membership.sector_code): membership.sector_name
            for membership in memberships
        }
        membership_counts = {
            (membership.sector_type.casefold(), membership.sector_code): membership.member_count
            for membership in memberships
        }
        for key, rows in grouped.items():
            ranked = tuple(
                feature.code
                for feature in sorted(rows, key=self._rank_value, reverse=True)
            )
            self._rankings[key] = ranked
            changes = [feature.change_pct for feature in rows]
            velocities = [
                feature.velocity_1m_pct
                for feature in rows
                if feature.velocity_1m_pct is not None
            ]
            amount_ratios = [
                feature.amount_ratio_1m
                for feature in rows
                if feature.amount_ratio_1m is not None
            ]
            up_count = sum(change > 0 for change in changes)
            up_ratio = up_count / len(rows)
            median_change = median(changes)
            median_velocity = median(velocities) if velocities else 0.0
            strong_count = sum(self._is_strong(feature) for feature in rows)
            amount_growth = median(amount_ratios) if amount_ratios else None
            gate = (
                len(rows) >= self.config.minimum_valid_members
                and median_change > 0
                and up_ratio > self.config.up_ratio_threshold
                and strong_count >= self.config.minimum_strong_count
            )
            gate_streak = self._gate_streaks.get(key, 0) + 1 if gate else 0
            next_gate_streaks[key] = gate_streak
            score = self._score(
                gate=gate,
                up_ratio=up_ratio,
                median_change=median_change,
                median_velocity=median_velocity,
                strong_count=strong_count,
                amount_growth=amount_growth,
                gate_streak=gate_streak,
            )
            metrics[key] = SectorMetrics(
                sector_code=key[1],
                sector_name=membership_names.get(key, key[1]),
                sector_type=key[0],
                member_count=max(membership_counts.get(key, len(rows)), len(rows)),
                valid_count=len(rows),
                up_count=up_count,
                up_ratio=up_ratio,
                median_change_pct=median_change,
                median_velocity_1m=median_velocity,
                strong_count=strong_count,
                amount_growth=amount_growth,
                score=score,
                gate_passed=gate,
                source_ts=max(feature.source_ts for feature in rows),
            )
        self._gate_streaks = next_gate_streaks
        return metrics

    def select_for_security(
        self,
        code: str,
        memberships: tuple[SectorMembership, ...],
        metrics: dict[tuple[str, str], SectorMetrics],
    ) -> SectorSelection | None:
        choices: list[SectorSelection] = []
        for membership in memberships:
            if membership.security.code != code:
                continue
            key = (membership.sector_type.casefold(), membership.sector_code)
            sector = metrics.get(key)
            ranking = self._rankings.get(key)
            if sector is None or ranking is None or code not in ranking:
                continue
            rank = ranking.index(code) + 1
            allowed_rank = min(
                self.config.candidate_rank_cap,
                max(1, math.ceil(sector.valid_count * self.config.candidate_rank_fraction)),
            )
            choices.append(
                SectorSelection(
                    metrics=sector,
                    rank=rank,
                    rank_percentile=rank / sector.valid_count,
                    gate_passed=sector.gate_passed and rank <= allowed_rank,
                )
            )
        if not choices:
            return None
        passing = [item for item in choices if item.gate_passed]
        ranked_choices = passing or choices
        ranked_choices.sort(
            key=lambda item: (
                -item.metrics.score,
                0 if item.metrics.sector_type == "industry" else 1,
                item.metrics.sector_code,
            )
        )
        best = ranked_choices[0]
        industry = next(
            (
                item
                for item in ranked_choices
                if item.metrics.sector_type == "industry"
            ),
            None,
        )
        if (
            industry is not None
            and best.metrics.score - industry.metrics.score
            <= self.config.industry_tie_margin
        ):
            return industry
        return best

    def _is_strong(self, feature: RollingFeatures) -> bool:
        return (
            feature.change_pct >= self.config.strong_change_pct
            or (
                feature.velocity_1m_pct is not None
                and feature.velocity_1m_pct >= self.config.strong_velocity_1m_pct
            )
        )

    @staticmethod
    def _rank_value(feature: RollingFeatures) -> tuple[float, float, str]:
        return (
            feature.change_pct,
            feature.velocity_1m_pct if feature.velocity_1m_pct is not None else -999.0,
            feature.code,
        )

    def _score(
        self,
        *,
        gate: bool,
        up_ratio: float,
        median_change: float,
        median_velocity: float,
        strong_count: int,
        amount_growth: float | None,
        gate_streak: int,
    ) -> float:
        score = 6.0 if gate else 0.0
        score += min(8.0, max(0.0, up_ratio * 8.0))
        score += min(5.0, max(0.0, median_change * 2.0))
        score += min(4.0, max(0.0, median_velocity * 4.0))
        score += min(4.0, strong_count / max(1, self.config.preferred_strong_count) * 4.0)
        if up_ratio >= self.config.preferred_up_ratio:
            score += 1.0
        if strong_count >= self.config.preferred_strong_count:
            score += 1.0
        if amount_growth is not None and amount_growth > 1:
            score += min(1.0, amount_growth - 1)
        if gate_streak >= self.config.persistence_bonus_rounds:
            score += self.config.persistence_bonus
        return round(min(30.0, score), 4)
