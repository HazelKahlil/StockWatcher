from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol

from stock_watcher.domain import (
    SHANGHAI,
    CandidateOutcome,
    DataQuality,
    OutcomeSlot,
    RealtimeQuote,
    SettlementMethod,
    classify_return,
    return_pct,
)
from stock_watcher.engine import CandidateBatch
from stock_watcher.providers.tushare.errors import ProviderError, ProviderFailureReason
from stock_watcher.providers.tushare.models import DataQuality as TransportQuality
from stock_watcher.providers.tushare.models import SourceTimestampKind as TransportTimestampKind
from stock_watcher.providers.tushare.models import TransportResult
from stock_watcher.storage import SQLiteStore


class OutcomeProvider(Protocol):
    def trading_dates(self, **params: str | int | float | bool) -> TransportResult: ...

    def realtime_quotes(self, codes: tuple[str, ...]) -> TransportResult: ...

    def historical_minutes(
        self,
        **params: str | int | float | bool,
    ) -> TransportResult: ...


@dataclass(frozen=True, slots=True)
class OutcomeActionReport:
    created: int = 0
    settled: int = 0
    pending: int = 0
    unavailable: int = 0
    skipped: int = 0
    safe_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _PricePoint:
    code: str
    price: float
    source_ts: datetime
    quality: str
    has_trade: bool


class CandidateOutcomeTracker:
    """Sidecar outcome lifecycle; failures never participate in candidate gates."""

    scheduled_triggers = {
        "scheduled-09:45": OutcomeSlot.MORNING,
        "scheduled-14:45": OutcomeSlot.AFTERNOON,
    }
    calendar_profile = "tushare_15000"
    calendar_endpoint = "/trade_cal"
    calendar_fields = ("exchange", "cal_date", "is_open", "pretrade_date")
    max_historical_attempts = 5

    def __init__(
        self,
        store: SQLiteStore,
        provider: OutcomeProvider,
        *,
        max_realtime_age_seconds: float = 120.0,
        max_future_skew_seconds: float = 10.0,
    ) -> None:
        self.store = store
        self.provider = provider
        self.max_realtime_age_seconds = max_realtime_age_seconds
        self.max_future_skew_seconds = max_future_skew_seconds

    @classmethod
    def pending_entries_for_scheduled_batch(
        cls,
        batch: CandidateBatch,
        *,
        snapshot_id: int,
        alert_id: int,
        trigger_type: str,
        recorded_at: datetime,
    ) -> list[dict[str, Any]]:
        """Build the three durable obligations without network/calendar I/O.

        The service uses these entries inside the same SQLite transaction as
        the scheduled alert.  Calendar resolution remains in the sidecar, but
        an abrupt process stop cannot lose the six daily rows between the
        alert commit and the sidecar executor.
        """
        slot = cls.scheduled_triggers.get(trigger_type)
        now = _shanghai(recorded_at)
        reason = _scheduled_batch_rejection(
            batch,
            slot=slot,
            recorded_at=now,
            max_entry_age_seconds=120.0,
            max_future_skew_seconds=10.0,
        )
        if reason is not None or slot is None:
            return []
        return cls._build_entries(
            batch,
            snapshot_id=snapshot_id,
            alert_id=alert_id,
            slot=slot,
            recorded_at=now,
            target_date=None,
            calendar_reason="calendar_pending",
        )

    @staticmethod
    def _build_entries(
        batch: CandidateBatch,
        *,
        snapshot_id: int,
        alert_id: int,
        slot: OutcomeSlot,
        recorded_at: datetime,
        target_date: date | None,
        calendar_reason: str | None,
    ) -> list[dict[str, Any]]:
        return [
            {
                "entry_snapshot_id": snapshot_id,
                "entry_alert_id": alert_id,
                "entry_trade_date": batch.source_ts.date().isoformat(),
                "slot": slot.value,
                "rank": rank,
                "code": candidate.code,
                "name": candidate.name,
                "entry_price": candidate.price,
                "entry_source_ts": candidate.source_ts.isoformat(),
                "target_trade_date": target_date.isoformat() if target_date else None,
                "target_slot": slot.value,
                "quality": "GOOD",
                "provider_version": candidate.provider_version,
                "config_version": candidate.config_version,
                "app_version": candidate.app_version,
                "created_at": recorded_at.isoformat(),
                "updated_at": recorded_at.isoformat(),
                "safe_reason": calendar_reason,
                "next_retry_at": (
                    _initial_retry_at(target_date, slot).isoformat()
                    if target_date
                    else None
                ),
            }
            for rank, candidate in enumerate(batch.candidates, start=1)
        ]

    def record_scheduled_batch(
        self,
        batch: CandidateBatch,
        *,
        snapshot_id: int,
        alert_id: int,
        trigger_type: str,
        recorded_at: datetime,
        resolve_calendar: bool = True,
    ) -> OutcomeActionReport:
        """Create exactly three idempotent pending rows after the alert is durable."""
        slot = self.scheduled_triggers.get(trigger_type)
        now = _shanghai(recorded_at)
        reason = _scheduled_batch_rejection(
            batch,
            slot=slot,
            recorded_at=now,
            max_entry_age_seconds=self.max_realtime_age_seconds,
            max_future_skew_seconds=self.max_future_skew_seconds,
        )
        if reason is not None:
            return OutcomeActionReport(skipped=3, safe_reasons=(reason,))
        assert slot is not None
        target_date: date | None = None
        calendar_reason: str | None = None
        if resolve_calendar:
            try:
                target_date = self.next_trading_date(batch.source_ts.date())
            except Exception as error:  # noqa: BLE001 - safely persisted for retry
                calendar_reason = _safe_failure("calendar", error)
        else:
            calendar_reason = "calendar_pending"
        entries = self._build_entries(
            batch,
            snapshot_id=snapshot_id,
            alert_id=alert_id,
            slot=slot,
            recorded_at=now,
            target_date=target_date,
            calendar_reason=calendar_reason,
        )
        inserted = self.store.create_candidate_outcomes(entries)
        if target_date is not None:
            for row in self.store.list_pending_candidate_outcomes(
                entry_snapshot_id=snapshot_id,
                limit=None,
            ):
                self.store.assign_candidate_outcome_target(
                    int(row["id"]),
                    target_trade_date=target_date.isoformat(),
                    next_retry_at=_initial_retry_at(target_date, slot).isoformat(),
                    updated_at=now.isoformat(),
                )
        return OutcomeActionReport(
            created=inserted,
            pending=3,
            safe_reasons=((calendar_reason,) if calendar_reason else ()),
        )

    def next_trading_date(self, entry_date: date) -> date:
        """Resolve the next open session only from the provider trade calendar."""
        start = entry_date + timedelta(days=1)
        end = entry_date + timedelta(days=21)
        result = self.provider.trading_dates(
            exchange="SSE",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            is_open="1",
        )
        open_dates = _validated_trade_calendar_open_dates(
            result,
            entry_date=entry_date,
            start_date=start,
            end_date=end,
            provider_profile=self.calendar_profile,
            endpoint=self.calendar_endpoint,
            fields=self.calendar_fields,
        )
        return open_dates[0]

    def resolve_pending_targets(
        self,
        *,
        now: datetime,
        limit: int = 100,
        entry_snapshot_id: int | None = None,
    ) -> OutcomeActionReport:
        rows = self.store.list_pending_candidate_outcomes(
            entry_snapshot_id=entry_snapshot_id,
            unresolved_only=True,
            limit=limit,
        )
        if not rows:
            return OutcomeActionReport()
        current = _shanghai(now)
        targets: dict[date, date] = {}
        reasons: list[str] = []
        resolved = 0
        for row in rows:
            entry_date = _parse_date(row.get("entry_trade_date"))
            if entry_date is None:
                reasons.append("invalid_entry_trade_date")
                continue
            if entry_date not in targets:
                try:
                    targets[entry_date] = self.next_trading_date(entry_date)
                except Exception as error:  # noqa: BLE001 - remains pending
                    reasons.append(_safe_failure("calendar", error))
                    continue
            target_slot = _parse_slot(row.get("target_slot"))
            if target_slot is None:
                reasons.append("invalid_target_slot")
                continue
            if self.store.assign_candidate_outcome_target(
                int(row["id"]),
                target_trade_date=targets[entry_date].isoformat(),
                next_retry_at=_initial_retry_at(
                    targets[entry_date],
                    target_slot,
                ).isoformat(),
                updated_at=current.isoformat(),
            ):
                resolved += 1
        return OutcomeActionReport(
            pending=len(rows),
            skipped=len(rows) - resolved,
            safe_reasons=tuple(sorted(set(reasons))),
        )

    def settle_fixed_slot(
        self,
        *,
        target_trade_date: date,
        slot: OutcomeSlot,
        scan_quotes: tuple[RealtimeQuote, ...],
        now: datetime,
    ) -> OutcomeActionReport:
        """Settle up to three due rows, reusing scan quotes before one batch request."""
        current = _shanghai(now)
        rows = self.store.list_pending_candidate_outcomes(
            target_trade_date=target_trade_date.isoformat(),
            target_slot=slot.value,
            limit=3,
        )
        if not rows:
            return OutcomeActionReport()
        settled = 0
        reasons: list[str] = []
        scan_points = {
            quote.security.code: _point_from_quote(quote)
            for quote in scan_quotes
            if quote.security.code in {str(row["code"]) for row in rows}
        }
        unresolved: list[dict[str, Any]] = []
        for row in rows:
            point = scan_points.get(str(row["code"]))
            reason = self._price_rejection(
                point,
                target_trade_date=target_trade_date,
                now=current,
            )
            if reason is not None:
                unresolved.append(row)
                reasons.append(f"realtime_scan:{reason}")
                continue
            assert point is not None
            settled += int(self._settle(row, point, SettlementMethod.REALTIME_SCAN, now=current))
        if unresolved:
            codes = tuple(str(row["code"]) for row in unresolved)
            try:
                result = self.provider.realtime_quotes(codes)
            except Exception as error:  # noqa: BLE001 - historical lane will retry
                reasons.append(_safe_failure("realtime_batch", error))
            else:
                batch_points = _points_from_realtime_result(result)
                for row in unresolved:
                    point = batch_points.get(str(row["code"]))
                    reason = self._price_rejection(
                        point,
                        target_trade_date=target_trade_date,
                        now=current,
                    )
                    if reason is not None:
                        reasons.append(f"realtime_batch:{reason}")
                        continue
                    assert point is not None
                    settled += int(
                        self._settle(
                            row,
                            point,
                            SettlementMethod.REALTIME_BATCH,
                            now=current,
                        )
                    )
        return OutcomeActionReport(
            settled=settled,
            pending=len(rows) - settled,
            safe_reasons=tuple(sorted(set(reasons))),
        )

    def backfill_recent_scheduled(
        self,
        *,
        now: datetime,
        days: int = 30,
        settlement_limit: int = 180,
    ) -> OutcomeActionReport:
        """Run the initial history lane with a truthful persisted lifecycle."""
        current = _shanghai(now)
        self._set_backfill_status(
            {
                "status": "running",
                "attempted_at": current.isoformat(),
                "message": "正在检查可验证的固定提醒历史……",
            }
        )
        try:
            return self._backfill_recent_scheduled(
                now=current,
                days=days,
                settlement_limit=settlement_limit,
            )
        except Exception as error:
            self._set_backfill_status(
                {
                    "status": "failed",
                    "attempted_at": current.isoformat(),
                    "safe_reason": _safe_failure("historical_backfill", error),
                    "message": "历史回补检查失败；从新固定提醒开始记录不受影响。",
                }
            )
            raise

    def _backfill_recent_scheduled(
        self,
        *,
        now: datetime,
        days: int = 30,
        settlement_limit: int = 180,
    ) -> OutcomeActionReport:
        """Create verifiable historical entries and serially settle exact minutes."""
        current = _shanghai(now)
        entries = self.store.list_scheduled_candidate_entries(now=current, days=days)
        grouped: dict[int, list[dict[str, Any]]] = {}
        for row in entries:
            grouped.setdefault(int(row["entry_alert_id"]), []).append(row)
        calendar_cache: dict[date, date] = {}
        reasons: list[str] = []
        created = 0
        skipped = 0
        for rows in grouped.values():
            prepared, reason = self._prepare_historical_entries(
                rows,
                current=current,
                calendar_cache=calendar_cache,
            )
            if reason is not None:
                skipped += len(rows)
                reasons.append(reason)
                continue
            created += self.store.create_candidate_outcomes(prepared)
        self.resolve_pending_targets(now=current, limit=settlement_limit)
        pending_rows = self.store.list_pending_candidate_outcomes(
            newest_first=True,
            limit=None,
        )
        due = [
            row
            for row in pending_rows
            if _historical_settlement_is_due(row, current)
        ][:settlement_limit]
        settled = 0
        unavailable = 0
        for row in due:
            if not _historical_settlement_is_due(row, current):
                continue
            result = self._settle_historical(
                row,
                now=current,
                final_confirmation=_historical_final_confirmation(row, current),
            )
            settled += result.settled
            unavailable += result.unavailable
            reasons.extend(result.safe_reasons)
        pending = len(self.store.list_pending_candidate_outcomes(limit=None))
        unverified = unavailable + skipped
        status = "completed" if unverified == 0 and pending == 0 else "partial"
        message = "可验证历史已回补；无法验证的数据不计入统计。"
        if status == "partial":
            message = f"已回补{settled}笔，{unverified}笔因缺少可验证行情未纳入统计。"
            if pending:
                message += f"另有{pending}笔等待重试。"
        summary = {
            "status": status,
            "attempted_at": current.isoformat(),
            "window_days": days,
            "created": created,
            "settled": settled,
            "unavailable": unavailable,
            "skipped": skipped,
            "pending": pending,
            "safe_reasons": dict(Counter(reasons)),
            "message": message,
        }
        self._set_backfill_status(summary)
        return OutcomeActionReport(
            created=created,
            settled=settled,
            pending=pending,
            unavailable=unavailable,
            skipped=skipped,
            safe_reasons=tuple(sorted(set(reasons))),
        )

    def _set_backfill_status(self, value: dict[str, object]) -> None:
        try:
            self.store.set_app_setting("candidate_outcome_backfill_status", value)
        except Exception:
            pass

    def backfill_due(
        self,
        *,
        now: datetime,
        target_trade_date: date,
        target_slot: OutcomeSlot,
        limit: int = 3,
    ) -> OutcomeActionReport:
        """Retry one exact target date/slot without allowing old backlog to steal its limit."""
        if limit < 1:
            raise ValueError("candidate outcome backfill limit must be positive")
        current = _shanghai(now)
        rows = self.store.list_pending_candidate_outcomes(
            target_trade_date=target_trade_date.isoformat(),
            target_slot=target_slot.value,
            limit=limit,
        )
        settled = 0
        unavailable = 0
        pending = 0
        reasons: list[str] = []
        for row in rows:
            if not _historical_settlement_is_due(row, current):
                continue
            result = self._settle_historical(
                row,
                now=current,
                final_confirmation=_historical_final_confirmation(row, current),
            )
            settled += result.settled
            unavailable += result.unavailable
            pending += result.pending
            reasons.extend(result.safe_reasons)
        return OutcomeActionReport(
            settled=settled,
            pending=pending,
            unavailable=unavailable,
            safe_reasons=tuple(sorted(set(reasons))),
        )

    def due_backfill_groups(
        self,
        *,
        now: datetime,
        limit: int = 4,
    ) -> tuple[tuple[date, OutcomeSlot], ...]:
        """Discover persisted due groups, newest first, so restarts resume safely."""
        if limit < 1:
            raise ValueError("candidate outcome due group limit must be positive")
        current = _shanghai(now)
        groups: set[tuple[date, OutcomeSlot]] = set()
        for row in self.store.list_pending_candidate_outcomes(newest_first=True, limit=None):
            if not _historical_settlement_is_due(row, current):
                continue
            target_date = _parse_date(row.get("target_trade_date"))
            target_slot = _parse_slot(row.get("target_slot"))
            if target_date is not None and target_slot is not None:
                groups.add((target_date, target_slot))
        return tuple(
            sorted(groups, key=lambda item: (item[0], item[1].value), reverse=True)[:limit]
        )

    def _prepare_historical_entries(
        self,
        rows: list[dict[str, Any]],
        *,
        current: datetime,
        calendar_cache: dict[date, date],
    ) -> tuple[list[dict[str, Any]], str | None]:
        if len(rows) != 3 or any(int(row.get("candidate_count", 0)) != 3 for row in rows):
            return [], "scheduled_snapshot_not_three"
        ranks = {int(row.get("rank", 0)) for row in rows}
        codes = {str(row.get("code", "")) for row in rows}
        if ranks != {1, 2, 3} or len(codes) != 3:
            return [], "scheduled_snapshot_not_unique_three"
        first = rows[0]
        slot = self.scheduled_triggers.get(str(first.get("trigger_type")))
        if slot is None or str(first.get("health")) != "HEALTHY":
            return [], "scheduled_snapshot_not_healthy"
        provider_version = str(first.get("provider_version") or "")
        if _forbidden_history_source(
            provider_version,
            str(first.get("config_version") or ""),
            str(first.get("app_version") or ""),
        ):
            return [], "replay_synthetic_or_mock_excluded"
        snapshot_source = _parse_datetime(first.get("snapshot_source_ts"))
        if snapshot_source is None:
            return [], "entry_source_ts_missing"
        entry_date = snapshot_source.date()
        target = calendar_cache.get(entry_date)
        if target is None:
            try:
                target = self.next_trading_date(entry_date)
            except Exception as error:  # noqa: BLE001 - no guessed calendar fallback
                return [], _safe_failure("calendar", error)
            calendar_cache[entry_date] = target
        prepared: list[dict[str, Any]] = []
        for row in rows:
            payload = _json_dict(row.get("candidate_payload_json"))
            if payload.get("is_formal") is not True or payload.get("is_supplement") is not False:
                return [], "scheduled_candidate_not_formal"
            source_ts = _parse_datetime(payload.get("source_ts"))
            entry_price = _positive_float(row.get("entry_price"))
            if (
                source_ts is None
                or source_ts.date() != entry_date
                or entry_price is None
                or str(row.get("provider_version")) != provider_version
            ):
                return [], "entry_price_timestamp_or_version_unverifiable"
            prepared.append(
                {
                    "entry_snapshot_id": int(row["entry_snapshot_id"]),
                    "entry_alert_id": int(row["entry_alert_id"]),
                    "entry_trade_date": entry_date.isoformat(),
                    "slot": slot.value,
                    "rank": int(row["rank"]),
                    "code": str(row["code"]),
                    "name": str(row["name"]),
                    "entry_price": entry_price,
                    "entry_source_ts": source_ts.isoformat(),
                    "target_trade_date": target.isoformat(),
                    "target_slot": slot.value,
                    "quality": "GOOD",
                    "provider_version": provider_version,
                    "config_version": str(row["config_version"]),
                    "app_version": str(row["app_version"]),
                    "created_at": current.isoformat(),
                    "updated_at": current.isoformat(),
                    "safe_reason": None,
                    "next_retry_at": _initial_retry_at(target, slot).isoformat(),
                }
            )
        return prepared, None

    def _settle_historical(
        self,
        row: dict[str, Any],
        *,
        now: datetime,
        final_confirmation: bool = False,
    ) -> OutcomeActionReport:
        target_date = _parse_date(row.get("target_trade_date"))
        slot = _parse_slot(row.get("target_slot"))
        if target_date is None or slot is None:
            return OutcomeActionReport(pending=1, safe_reasons=("target_unresolved",))
        target_ts = datetime.combine(
            target_date,
            time.fromisoformat(slot.value),
            tzinfo=SHANGHAI,
        )
        final = final_confirmation or _historical_final_confirmation(row, now)
        attempt_number = int(row.get("settlement_attempts") or 0) + 1
        next_retry = (
            None
            if final
            else _next_historical_retry_at(
                target_ts,
                now=now,
                attempt_number=attempt_number,
            )
        )
        if not self.store.record_candidate_outcome_attempt(
            int(row["id"]),
            attempted_at=now.isoformat(),
            next_retry_at=(next_retry.isoformat() if next_retry else None),
        ):
            return OutcomeActionReport(skipped=1)
        try:
            result = self.provider.historical_minutes(
                ts_code=str(row["code"]),
                freq="1min",
                start_date=target_ts.strftime("%Y-%m-%d %H:%M:%S"),
                end_date=target_ts.strftime("%Y-%m-%d %H:%M:%S"),
            )
        except ProviderError as error:
            if error.reason is ProviderFailureReason.EMPTY_DATA:
                reason = "historical_minute_missing_or_ambiguous"
                if final:
                    changed = self.store.mark_candidate_outcome_unavailable(
                        int(row["id"]),
                        quality="UNAVAILABLE",
                        safe_reason=reason,
                        updated_at=now.isoformat(),
                    )
                    return OutcomeActionReport(
                        unavailable=int(changed),
                        safe_reasons=(reason,),
                    )
                return self._defer_historical(
                    row,
                    reason=reason,
                    next_retry=next_retry,
                    now=now,
                )
            return self._defer_historical(
                row,
                reason=_safe_failure("historical_minute", error),
                next_retry=(
                    _next_transient_retry_at(now, attempt_number)
                    if final
                    else next_retry
                ),
                now=now,
            )
        except Exception as error:  # noqa: BLE001 - leave pending for retry
            return self._defer_historical(
                row,
                reason=_safe_failure("historical_minute", error),
                next_retry=(
                    _next_transient_retry_at(now, attempt_number)
                    if final
                    else next_retry
                ),
                now=now,
            )
        point, point_reason = _point_from_historical_result(
            result,
            code=str(row["code"]),
            target_ts=target_ts,
        )
        if point is None:
            safe_reason = point_reason or "historical_minute_unavailable"
            conclusive = safe_reason in {
                "historical_minute_missing_or_ambiguous",
                "historical_minute_suspended_or_no_trade",
            }
            if final and conclusive:
                changed = self.store.mark_candidate_outcome_unavailable(
                    int(row["id"]),
                    quality="UNAVAILABLE",
                    safe_reason=safe_reason,
                    updated_at=now.isoformat(),
                )
                return OutcomeActionReport(
                    unavailable=int(changed),
                    safe_reasons=(safe_reason,),
                )
            return self._defer_historical(
                row,
                reason=safe_reason,
                next_retry=(
                    _next_transient_retry_at(now, attempt_number)
                    if final
                    else next_retry
                ),
                now=now,
            )
        changed = self._settle(
            row,
            point,
            SettlementMethod.HISTORICAL_MINUTE,
            now=now,
        )
        return OutcomeActionReport(settled=int(changed))

    def _defer_historical(
        self,
        row: dict[str, Any],
        *,
        reason: str,
        next_retry: datetime | None,
        now: datetime,
    ) -> OutcomeActionReport:
        changed = self.store.defer_candidate_outcome(
            int(row["id"]),
            safe_reason=reason,
            next_retry_at=(next_retry.isoformat() if next_retry else None),
            updated_at=now.isoformat(),
        )
        return OutcomeActionReport(
            pending=int(changed),
            skipped=int(not changed),
            safe_reasons=(reason,),
        )

    def _settle(
        self,
        row: dict[str, Any],
        point: _PricePoint,
        method: SettlementMethod,
        *,
        now: datetime,
    ) -> bool:
        entry_price = float(row["entry_price"])
        value = return_pct(entry_price, point.price)
        outcome = classify_return(value)
        return self.store.settle_candidate_outcome(
            int(row["id"]),
            exit_price=point.price,
            exit_source_ts=point.source_ts.isoformat(),
            return_pct=value,
            outcome=outcome.value,
            settlement_method=method.value,
            quality=point.quality,
            updated_at=now.isoformat(),
        )

    def _price_rejection(
        self,
        point: _PricePoint | None,
        *,
        target_trade_date: date,
        now: datetime,
    ) -> str | None:
        if point is None:
            return "missing"
        if point.price <= 0:
            return "non_positive_price"
        if point.source_ts.date() != target_trade_date:
            return "wrong_trade_date"
        age = (now - point.source_ts).total_seconds()
        if age > self.max_realtime_age_seconds:
            return "stale_source_ts"
        if age < -self.max_future_skew_seconds:
            return "future_source_ts"
        if point.quality != "GOOD":
            return "quality_not_good"
        if not point.has_trade:
            return "suspended_or_no_trade"
        return None


def candidate_outcome_rows(
    store: SQLiteStore,
    *,
    trading_days: int | None,
) -> tuple[CandidateOutcome, ...]:
    return tuple(
        CandidateOutcome.from_mapping(row)
        for row in store.list_candidate_outcomes(trading_days=trading_days)
    )


def _scheduled_batch_rejection(
    batch: CandidateBatch,
    *,
    slot: OutcomeSlot | None,
    recorded_at: datetime,
    max_entry_age_seconds: float,
    max_future_skew_seconds: float,
) -> str | None:
    if slot is None:
        return "trigger_not_scheduled"
    if (
        batch.health.value != "HEALTHY"
        or len(batch.candidates) != 3
        or batch.formal_count != 3
        or any(not candidate.is_formal or candidate.is_supplement for candidate in batch.candidates)
    ):
        return "scheduled_batch_not_healthy_three"
    if batch.source_ts.date() != recorded_at.date():
        return "scheduled_batch_wrong_trade_date"
    age = (recorded_at - batch.source_ts).total_seconds()
    if age > max_entry_age_seconds:
        return "scheduled_batch_stale_source_ts"
    if age < -max_future_skew_seconds:
        return "scheduled_batch_future_source_ts"
    codes = {candidate.code for candidate in batch.candidates}
    if len(codes) != 3:
        return "scheduled_batch_duplicate_code"
    for candidate in batch.candidates:
        if candidate.price <= 0 or candidate.source_ts.date() != batch.source_ts.date():
            return "scheduled_candidate_price_or_time_invalid"
        if _forbidden_history_source(
            candidate.provider_version,
            candidate.config_version,
            candidate.app_version,
        ):
            return "replay_synthetic_or_mock_excluded"
    return None


def _forbidden_history_source(*versions: str) -> bool:
    joined = " ".join(versions).lower()
    return any(marker in joined for marker in ("replay", "synthetic", "mock", "demo"))


def _point_from_quote(quote: RealtimeQuote) -> _PricePoint:
    return _PricePoint(
        code=quote.security.code,
        price=quote.price,
        source_ts=_shanghai(quote.source_ts),
        quality="GOOD" if quote.quality is DataQuality.GOOD else quote.quality.value,
        has_trade=(
            quote.trading_state.lower() not in {"suspended", "halted"}
            and (quote.volume_shares > 0 or quote.amount_cny > 0)
        ),
    )


def _points_from_realtime_result(result: TransportResult) -> dict[str, _PricePoint]:
    if result.provenance.quality is not TransportQuality.HEALTHY:
        return {}
    output: dict[str, _PricePoint] = {}
    for record in result.records:
        code = str(record.get("ts_code") or "")
        source_ts = _parse_datetime(record.get("source_ts"))
        price = _positive_float(record.get("price"))
        quality = str(record.get("data_quality") or "")
        volume = _nonnegative_float(record.get("vol"))
        amount = _nonnegative_float(record.get("amount"))
        if not code or source_ts is None or price is None:
            continue
        output[code] = _PricePoint(
            code=code,
            price=price,
            source_ts=source_ts,
            quality="GOOD" if quality == "HEALTHY" else quality or "UNAVAILABLE",
            has_trade=(volume or 0) > 0 or (amount or 0) > 0,
        )
    return output


def _point_from_historical_result(
    result: TransportResult,
    *,
    code: str,
    target_ts: datetime,
) -> tuple[_PricePoint | None, str | None]:
    if result.provenance.quality is not TransportQuality.HEALTHY:
        return None, "historical_minute_quality_not_healthy"
    matching: list[_PricePoint] = []
    for record in result.records:
        record_code = str(record.get("ts_code") or code)
        source_ts = _historical_timestamp(record)
        close = _positive_float(record.get("close"))
        volume = _nonnegative_float(record.get("vol"))
        amount = _nonnegative_float(record.get("amount"))
        if record_code != code or source_ts is None or close is None:
            continue
        if source_ts.replace(second=0, microsecond=0) != target_ts:
            continue
        matching.append(
            _PricePoint(
                code=code,
                price=close,
                source_ts=source_ts,
                quality="GOOD",
                has_trade=(volume or 0) > 0 or (amount or 0) > 0,
            )
        )
    if len(matching) != 1:
        return None, "historical_minute_missing_or_ambiguous"
    if not matching[0].has_trade:
        return None, "historical_minute_suspended_or_no_trade"
    return matching[0], None


def _historical_timestamp(record: Mapping[str, object]) -> datetime | None:
    for key in ("trade_time", "datetime", "source_ts"):
        parsed = _parse_datetime(record.get(key))
        if parsed is not None:
            return parsed
    return None


def _historical_settlement_is_due(
    row: dict[str, Any],
    now: datetime,
) -> bool:
    target_date = _parse_date(row.get("target_trade_date"))
    slot = _parse_slot(row.get("target_slot"))
    if target_date is None or slot is None:
        return False
    target_ts = datetime.combine(
        target_date,
        time.fromisoformat(slot.value),
        tzinfo=SHANGHAI,
    )
    if now < target_ts + timedelta(minutes=1):
        return False
    if (
        int(row.get("settlement_attempts") or 0)
        >= CandidateOutcomeTracker.max_historical_attempts
    ):
        return False
    next_retry = _parse_datetime(row.get("next_retry_at"))
    if next_retry is None:
        return int(row.get("settlement_attempts") or 0) == 0
    return now >= next_retry


def _initial_retry_at(target_date: date, slot: OutcomeSlot) -> datetime:
    return datetime.combine(
        target_date,
        time.fromisoformat(slot.value),
        tzinfo=SHANGHAI,
    ) + timedelta(minutes=1)


def _next_historical_retry_at(
    target_ts: datetime,
    *,
    now: datetime,
    attempt_number: int,
) -> datetime | None:
    if attempt_number >= CandidateOutcomeTracker.max_historical_attempts:
        return None
    close_confirmation = datetime.combine(
        target_ts.date(),
        time(15, 5),
        tzinfo=SHANGHAI,
    )
    schedule = sorted(
        {
            target_ts + timedelta(minutes=1),
            target_ts + timedelta(minutes=3),
            target_ts + timedelta(minutes=8),
            target_ts + timedelta(minutes=20),
            close_confirmation,
        }
    )
    return next((candidate for candidate in schedule if candidate > now), None)


def _next_transient_retry_at(now: datetime, attempt_number: int) -> datetime | None:
    """Keep post-close transport failures recoverable without creating an infinite loop."""
    retry_delays = {
        1: timedelta(minutes=3),
        2: timedelta(minutes=8),
        3: timedelta(minutes=20),
        4: timedelta(minutes=30),
    }
    delay = retry_delays.get(attempt_number)
    return now + delay if delay is not None else None


def _historical_final_confirmation(row: Mapping[str, object], now: datetime) -> bool:
    target_date = _parse_date(row.get("target_trade_date"))
    slot = _parse_slot(row.get("target_slot"))
    if target_date is None or slot is None:
        return False
    if target_date < now.date():
        return True
    if target_date > now.date():
        return False
    target_ts = datetime.combine(
        target_date,
        time.fromisoformat(slot.value),
        tzinfo=SHANGHAI,
    )
    return now >= max(
        target_ts + timedelta(minutes=20),
        datetime.combine(target_date, time(15, 5), tzinfo=SHANGHAI),
    )


def _validated_trade_calendar_open_dates(
    result: TransportResult,
    *,
    entry_date: date,
    start_date: date,
    end_date: date,
    provider_profile: str,
    endpoint: str,
    fields: tuple[str, ...],
) -> list[date]:
    provenance = result.provenance
    if provenance.provider_profile != provider_profile:
        raise ValueError("trade calendar provider profile is not controlled")
    if provenance.endpoint != endpoint or tuple(provenance.fields_used) != fields:
        raise ValueError("trade calendar route contract changed")
    if not _valid_received_timestamp(provenance.received_ts):
        raise ValueError("trade calendar received timestamp is invalid")
    if provenance.quality is not TransportQuality.DEGRADED:
        raise ValueError("trade calendar quality is not accepted")
    kind = getattr(
        provenance.source_timestamp_kind,
        "value",
        str(provenance.source_timestamp_kind),
    )
    if provenance.source_ts is not None or kind not in {
        TransportTimestampKind.MISSING.value,
        "received_fallback",
    }:
        raise ValueError("trade calendar degraded provenance is not expected")
    if not result.records:
        raise ValueError("trade calendar response is empty")

    expected_fields = set(fields)
    states: dict[date, bool] = {}
    for record in result.records:
        record_fields = set(record)
        if not {"cal_date", "is_open"} <= record_fields or not record_fields <= expected_fields:
            raise ValueError("trade calendar record schema changed")
        exchange = record.get("exchange")
        if exchange not in (None, "", "SSE"):
            raise ValueError("trade calendar exchange is unexpected")
        calendar_date = _parse_date(record.get("cal_date"))
        if calendar_date is None:
            raise ValueError("trade calendar date is invalid")
        if not start_date <= calendar_date <= end_date:
            raise ValueError("trade calendar date is outside request range")
        pretrade = record.get("pretrade_date")
        if pretrade not in (None, "") and _parse_date(pretrade) is None:
            raise ValueError("trade calendar pretrade date is invalid")
        is_open = _parse_open_flag(record.get("is_open"))
        if is_open is None:
            raise ValueError("trade calendar open flag is invalid")
        previous = states.get(calendar_date)
        if previous is not None and previous is not is_open:
            raise ValueError("trade calendar has contradictory duplicate dates")
        states[calendar_date] = is_open

    open_dates = sorted(
        calendar_date
        for calendar_date, is_open in states.items()
        if is_open and calendar_date > entry_date
    )
    if not open_dates:
        raise ValueError("trade calendar has no next open date")
    return open_dates


def _valid_received_timestamp(value: object) -> bool:
    if not isinstance(value, datetime) or value.tzinfo is None:
        return False
    try:
        if value.utcoffset() is None:
            return False
        datetime.fromisoformat(value.isoformat())
        value.astimezone(SHANGHAI)
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def _parse_open_flag(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    return None


def _parse_slot(value: object) -> OutcomeSlot | None:
    try:
        return OutcomeSlot(str(value))
    except ValueError:
        return None


def _parse_date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    for pattern in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _shanghai(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return _shanghai(datetime.fromisoformat(text))
    except ValueError:
        pass
    for pattern in ("%Y%m%d%H%M%S", "%Y%m%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=SHANGHAI)
        except ValueError:
            continue
    return None


def _json_dict(value: object) -> dict[str, object]:
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _positive_float(value: object) -> float | None:
    parsed = _nonnegative_float(value)
    return parsed if parsed is not None and parsed > 0 else None


def _nonnegative_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _safe_failure(stage: str, error: Exception) -> str:
    if isinstance(error, ProviderError):
        reason = getattr(error.reason, "value", str(error.reason))
        return f"{stage}:{reason}"
    return f"{stage}:{type(error).__name__}"


def _shanghai(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)
