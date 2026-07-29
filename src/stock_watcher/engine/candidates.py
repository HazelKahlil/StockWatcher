from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime

from stock_watcher.domain import SHANGHAI, CandidateInput, HealthState


@dataclass(frozen=True, slots=True)
class CandidateConfig:
    version: str
    app_version: str
    strong_change_pct: float = 5.0
    strong_velocity_pct: float = 2.0
    sector_pass_strength: float = 2.0
    medium_score: float = 7.0


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
    # Display fields are copied from the point-in-time input so the UI never
    # has to re-read provider payloads or infer prices from a score.
    price: float = 0.0
    change_pct: float = 0.0
    velocity_pct: float = 0.0


@dataclass(frozen=True, slots=True)
class CandidateBatch:
    source_ts: datetime
    generated_at: datetime
    candidates: tuple[Candidate, ...]
    health: HealthState
    overall_weak: bool
    fund_module: str = "unavailable"

    def trace_payload(self) -> str:
        """Canonical JSON retained with every snapshot for replay and audit."""
        return json.dumps(asdict(self), default=str, sort_keys=True, separators=(",", ":"))


class CandidateEngine:
    """Pure calculation: no clock, IO, provider dictionaries, or alert state."""

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
        # Stable code tie-breaker is part of the replay contract.
        evaluated.sort(key=lambda candidate: (-candidate.score, candidate.code))
        complete = [candidate for candidate in evaluated if candidate.level != "近"]
        selected = complete[:3]
        if len(selected) < 3:
            selected.extend(candidate for candidate in evaluated if candidate not in selected)
            selected = selected[:3]
        source_ts = max(item.source_ts for item in inputs)
        generated_at = max(item.received_ts for item in inputs)
        return CandidateBatch(
            source_ts=source_ts,
            generated_at=generated_at,
            candidates=tuple(selected),
            health=health,
            overall_weak=len(complete) < 3,
        )

    def _evaluate(self, item: CandidateInput, config: CandidateConfig) -> Candidate:
        price_score = round(item.change_pct + item.velocity_pct, 4)
        sector_score = round(item.sector_strength, 4)
        trend_score = round(item.trend_3d_pct, 4)
        penalty = 0.0 if item.trend_3d_pct >= 0 else round(abs(item.trend_3d_pct), 4)
        score = round(price_score + sector_score + trend_score - penalty, 4)
        sector_passed = item.sector_strength >= config.sector_pass_strength
        price_strong = (
            item.change_pct >= config.strong_change_pct
            and item.velocity_pct >= config.strong_velocity_pct
        )
        reasons = [
            f"涨幅 {item.change_pct:.2f}%",
            f"涨速 {item.velocity_pct:.2f}%",
            f"板块 {item.sector} {item.sector_strength:.2f}",
            f"三日趋势 {item.trend_3d_pct:.2f}%",
        ]
        if not sector_passed:
            level = "近"
            reasons.append("板块未通过，最高仅近")
        elif price_strong and item.trend_3d_pct >= 0:
            level = "强"
        elif score >= config.medium_score:
            level = "中"
        else:
            level = "近"
            reasons.append("完整信号不足，作为近候选补足")
        if penalty:
            reasons.append(f"趋势处罚 {penalty:.2f}")
        return Candidate(
            code=item.security.code,
            name=item.security.name,
            sector=item.sector,
            level=level,
            score=score,
            price_score=price_score,
            sector_score=sector_score,
            trend_score=trend_score,
            penalty=penalty,
            reasons=tuple(reasons),
            source_ts=item.source_ts,
            provider_version=item.provider_version,
            config_version=config.version,
            app_version=config.app_version,
            price=item.price,
            change_pct=item.change_pct,
            velocity_pct=item.velocity_pct,
        )
