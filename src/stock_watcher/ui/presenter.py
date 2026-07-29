from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from stock_watcher.domain import HealthState
from stock_watcher.engine.candidates import CandidateBatch


@dataclass(frozen=True, slots=True)
class CandidateRow:
    code: str
    name: str
    price: float
    change_pct: float
    velocity_pct: float
    level: str
    score: float
    sector: str
    reasons: tuple[str, ...]
    source_ts: datetime
    provider_version: str
    config_version: str
    fund_module: str
    fund_label: str
    trend_label: str
    is_formal: bool
    is_supplement: bool


@dataclass(frozen=True, slots=True)
class UiSnapshot:
    health: HealthState
    health_detail: str
    source_label: str
    phase_label: str
    last_updated: datetime | None
    overall_label: str
    candidates: tuple[CandidateRow, ...]
    previous_candidates: tuple[CandidateRow, ...]
    fund_label: str
    alert_allowed: bool


def snapshot_from_batch(
    batch: CandidateBatch | None,
    *,
    health: HealthState,
    health_detail: str = "",
    source_label: str = "Mock / Replay（模拟/回放数据）",
    phase_label: str = "盘中观察",
) -> UiSnapshot:
    """Turn one immutable engine result into the UI's read-only view model."""
    candidates: tuple[CandidateRow, ...] = ()
    previous_candidates: tuple[CandidateRow, ...] = ()
    last_updated: datetime | None = None
    overall_label = "暂无候选"
    fund_label = "资金未确认"
    if batch is not None:
        rows = tuple(
            CandidateRow(
                code=item.code,
                name=item.name,
                price=item.price,
                change_pct=item.change_pct,
                velocity_pct=item.velocity_pct,
                level=item.level,
                score=item.score,
                sector=item.sector,
                reasons=item.reasons,
                source_ts=item.source_ts,
                provider_version=item.provider_version,
                config_version=item.config_version,
                fund_module=batch.fund_module,
                fund_label=item.fund_label,
                trend_label=item.trend_label,
                is_formal=item.is_formal,
                is_supplement=item.is_supplement,
            )
            for item in batch.candidates
        )
        last_updated = batch.generated_at
        if health is HealthState.HEALTHY:
            candidates = rows
            overall_label = "本轮整体偏弱" if batch.overall_weak else "运行正常"
        else:
            previous_candidates = rows
    if health is HealthState.STOPPED:
        overall_label = "数据中断"
    elif health in (HealthState.STALE, HealthState.WARMING):
        overall_label = "数据未就绪"
    return UiSnapshot(
        health=health,
        health_detail=health_detail,
        source_label=source_label,
        phase_label=phase_label,
        last_updated=last_updated,
        overall_label=overall_label,
        candidates=candidates,
        previous_candidates=previous_candidates,
        fund_label=fund_label,
        alert_allowed=health is HealthState.HEALTHY and bool(candidates),
    )


def format_change(value: float) -> str:
    return f"{value:+.2f}%"


def format_time(value: datetime | None) -> str:
    return "—" if value is None else value.strftime("%Y-%m-%d %H:%M")


def detail_reasons(row: CandidateRow) -> tuple[tuple[str, str], ...]:
    """Translate deterministic engine signals into short trader-facing copy."""
    titles = ("当前表现", "短线动能", "板块表现", "成交与趋势", "资金情况")
    reasons = list(row.reasons[:4])
    if not reasons:
        reasons = [
            f"{row.sector}板块提供当前观察依据。",
            f"当前涨幅 {format_change(row.change_pct)}，1分钟涨速"
            f" {format_change(row.velocity_pct)}。",
            f"最近三日趋势为{row.trend_label}。",
        ]
    reasons.append(row.fund_label)
    return tuple(
        (titles[index], reason)
        for index, reason in enumerate(reasons[:5])
    )
