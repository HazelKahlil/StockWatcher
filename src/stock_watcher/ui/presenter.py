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


@dataclass(frozen=True, slots=True)
class UiSnapshot:
    health: HealthState
    health_detail: str
    source_label: str
    phase_label: str
    last_updated: datetime | None
    overall_label: str
    candidates: tuple[CandidateRow, ...]
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
    last_updated: datetime | None = None
    overall_label = "暂无候选"
    fund_label = "资金模块：未就绪（M0 未通过）"
    if batch is not None and health is HealthState.HEALTHY:
        candidates = tuple(
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
            )
            for item in batch.candidates
        )
        last_updated = batch.generated_at
        overall_label = "整体偏弱" if batch.overall_weak else "整体正常"
    if health is HealthState.STOPPED:
        overall_label = "数据中断，停止产生新候选"
    elif health in (HealthState.STALE, HealthState.WARMING):
        overall_label = "数据未就绪，不产生新候选"
    return UiSnapshot(
        health=health,
        health_detail=health_detail,
        source_label=source_label,
        phase_label=phase_label,
        last_updated=last_updated,
        overall_label=overall_label,
        candidates=candidates,
        fund_label=fund_label,
        alert_allowed=health is HealthState.HEALTHY and bool(candidates),
    )


def format_change(value: float) -> str:
    return f"{value:+.2f}%"


def format_time(value: datetime | None) -> str:
    return "—" if value is None else value.strftime("%Y-%m-%d %H:%M:%S")
