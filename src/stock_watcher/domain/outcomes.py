from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from statistics import median
from typing import Any


class OutcomeSlot(StrEnum):
    MORNING = "09:45"
    AFTERNOON = "14:45"


class OutcomeStatus(StrEnum):
    PENDING = "pending"
    SETTLED = "settled"
    UNAVAILABLE = "unavailable"


class OutcomeResult(StrEnum):
    WIN = "win"
    LOSS = "loss"
    FLAT = "flat"


class SettlementMethod(StrEnum):
    REALTIME_SCAN = "realtime_scan"
    REALTIME_BATCH = "realtime_batch"
    HISTORICAL_MINUTE = "historical_minute"


@dataclass(frozen=True, slots=True)
class CandidateOutcome:
    id: int
    entry_snapshot_id: int
    entry_alert_id: int
    entry_trade_date: date
    slot: OutcomeSlot
    rank: int
    code: str
    name: str
    entry_price: float
    entry_source_ts: datetime
    target_trade_date: date | None
    target_slot: OutcomeSlot
    exit_price: float | None
    exit_source_ts: datetime | None
    return_pct: float | None
    status: OutcomeStatus
    outcome: OutcomeResult | None
    settlement_method: SettlementMethod | None
    quality: str
    provider_version: str
    config_version: str
    app_version: str
    created_at: datetime
    updated_at: datetime
    safe_reason: str | None = None

    @classmethod
    def from_mapping(cls, row: dict[str, Any]) -> CandidateOutcome:
        status = OutcomeStatus(str(row["status"]))
        stored_return = (
            float(row["return_pct"]) if row.get("return_pct") is not None else None
        )
        return cls(
            id=int(row["id"]),
            entry_snapshot_id=int(row["entry_snapshot_id"]),
            entry_alert_id=int(row["entry_alert_id"]),
            entry_trade_date=date.fromisoformat(str(row["entry_trade_date"])),
            slot=OutcomeSlot(str(row["slot"])),
            rank=int(row["rank"]),
            code=str(row["code"]),
            name=str(row["name"]),
            entry_price=float(row["entry_price"]),
            entry_source_ts=datetime.fromisoformat(str(row["entry_source_ts"])),
            target_trade_date=(
                date.fromisoformat(str(row["target_trade_date"]))
                if row.get("target_trade_date")
                else None
            ),
            target_slot=OutcomeSlot(str(row["target_slot"])),
            exit_price=(float(row["exit_price"]) if row.get("exit_price") is not None else None),
            exit_source_ts=(
                datetime.fromisoformat(str(row["exit_source_ts"]))
                if row.get("exit_source_ts")
                else None
            ),
            return_pct=stored_return,
            status=status,
            # The realized entry-to-exit return is the source of truth.  Older
            # rows may contain an outcome derived from the wrong day-over-day
            # change percentage (for example +10% -> +8% was marked loss).
            outcome=(
                classify_return(stored_return)
                if status is OutcomeStatus.SETTLED and stored_return is not None
                else (OutcomeResult(str(row["outcome"])) if row.get("outcome") else None)
            ),
            settlement_method=(
                SettlementMethod(str(row["settlement_method"]))
                if row.get("settlement_method")
                else None
            ),
            quality=str(row.get("quality") or "unverified"),
            provider_version=str(row["provider_version"]),
            config_version=str(row["config_version"]),
            app_version=str(row["app_version"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            safe_reason=(str(row["safe_reason"]) if row.get("safe_reason") else None),
        )


@dataclass(frozen=True, slots=True)
class OutcomeStats:
    total_count: int
    settled_count: int
    win_count: int
    loss_count: int
    flat_count: int
    win_rate: float | None
    average_return_pct: float | None
    median_return_pct: float | None


@dataclass(frozen=True, slots=True)
class DailyPortfolioOutcome:
    entry_trade_date: date
    total_count: int
    settled_count: int
    complete: bool
    average_return_pct: float | None
    won: bool | None


@dataclass(frozen=True, slots=True)
class OutcomeReview:
    records: tuple[CandidateOutcome, ...]
    overall: OutcomeStats
    morning: OutcomeStats
    afternoon: OutcomeStats
    portfolios: tuple[DailyPortfolioOutcome, ...]
    portfolio_win_rate: float | None
    complete_portfolio_days: int
    portfolio_win_days: int


def return_pct(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0 or exit_price <= 0:
        raise ValueError("entry and exit prices must be positive")
    return (exit_price / entry_price - 1.0) * 100.0


def classify_return(value: float) -> OutcomeResult:
    if value > 0:
        return OutcomeResult.WIN
    if value < 0:
        return OutcomeResult.LOSS
    return OutcomeResult.FLAT


def build_outcome_review(records: tuple[CandidateOutcome, ...]) -> OutcomeReview:
    # Keep callers that construct domain records directly on the same safe
    # rule as database-backed callers: wins/losses are based on realized
    # entry-to-exit return, never on a persisted or display-time label.
    records = tuple(_canonicalize_result(record) for record in records)
    morning_records = tuple(record for record in records if record.slot is OutcomeSlot.MORNING)
    afternoon_records = tuple(
        record for record in records if record.slot is OutcomeSlot.AFTERNOON
    )
    by_date: dict[date, list[CandidateOutcome]] = {}
    for record in records:
        by_date.setdefault(record.entry_trade_date, []).append(record)
    portfolios: list[DailyPortfolioOutcome] = []
    for entry_date in sorted(by_date, reverse=True):
        rows = by_date[entry_date]
        settled_returns = [
            float(row.return_pct)
            for row in rows
            if row.status is OutcomeStatus.SETTLED and row.return_pct is not None
        ]
        morning_count = sum(row.slot is OutcomeSlot.MORNING for row in rows)
        afternoon_count = sum(row.slot is OutcomeSlot.AFTERNOON for row in rows)
        complete = (
            len(rows) == 6
            and len(settled_returns) == 6
            and morning_count == 3
            and afternoon_count == 3
        )
        average = sum(settled_returns) / 6 if complete else None
        portfolios.append(
            DailyPortfolioOutcome(
                entry_trade_date=entry_date,
                total_count=len(rows),
                settled_count=len(settled_returns),
                complete=complete,
                average_return_pct=average,
                won=average > 0 if average is not None else None,
            )
        )
    complete_days = [portfolio for portfolio in portfolios if portfolio.complete]
    win_days = sum(portfolio.won is True for portfolio in complete_days)
    return OutcomeReview(
        records=records,
        overall=_statistics(records),
        morning=_statistics(morning_records),
        afternoon=_statistics(afternoon_records),
        portfolios=tuple(portfolios),
        portfolio_win_rate=(win_days / len(complete_days) if complete_days else None),
        complete_portfolio_days=len(complete_days),
        portfolio_win_days=win_days,
    )


def _canonicalize_result(record: CandidateOutcome) -> CandidateOutcome:
    if record.status is OutcomeStatus.SETTLED and record.return_pct is not None:
        return replace(record, outcome=classify_return(float(record.return_pct)))
    return record


def _statistics(records: tuple[CandidateOutcome, ...]) -> OutcomeStats:
    settled = tuple(
        record
        for record in records
        if record.status is OutcomeStatus.SETTLED
        and record.outcome is not None
        and record.return_pct is not None
    )
    returns = [float(record.return_pct) for record in settled if record.return_pct is not None]
    wins = sum(record.outcome is OutcomeResult.WIN for record in settled)
    losses = sum(record.outcome is OutcomeResult.LOSS for record in settled)
    flats = sum(record.outcome is OutcomeResult.FLAT for record in settled)
    return OutcomeStats(
        total_count=len(records),
        settled_count=len(settled),
        win_count=wins,
        loss_count=losses,
        flat_count=flats,
        win_rate=(wins / len(settled) if settled else None),
        average_return_pct=(sum(returns) / len(returns) if returns else None),
        median_return_pct=(float(median(returns)) if returns else None),
    )
