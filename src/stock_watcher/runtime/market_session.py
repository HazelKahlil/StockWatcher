from __future__ import annotations

from datetime import date, datetime, time

from stock_watcher.engine import AlertTrigger


class MarketSessionSchedule:
    """Asia/Shanghai trading-session decisions driven by an injected clock."""

    morning_start = time(9, 30)
    morning_end = time(11, 30)
    afternoon_start = time(13, 0)
    afternoon_end = time(15, 0)

    def is_trading(self, now: datetime, open_dates: tuple[date, ...]) -> bool:
        if now.date() not in open_dates:
            return False
        return self.is_session_time(now)

    def is_session_time(self, now: datetime) -> bool:
        current = now.timetz().replace(tzinfo=None)
        return (
            self.morning_start <= current <= self.morning_end
            or self.afternoon_start <= current <= self.afternoon_end
        )

    @staticmethod
    def fixed_trigger(now: datetime) -> AlertTrigger | None:
        current = now.timetz().replace(tzinfo=None)
        if current.hour == 9 and current.minute == 45:
            return AlertTrigger.SCHEDULED_0945
        if current.hour == 14 and current.minute == 45:
            return AlertTrigger.SCHEDULED_1445
        return None

    @staticmethod
    def crossed_fixed_trigger(
        started_at: datetime,
        completed_at: datetime,
    ) -> AlertTrigger | None:
        if started_at.date() != completed_at.date() or completed_at < started_at:
            return None
        for target_time, trigger in (
            (time(9, 45), AlertTrigger.SCHEDULED_0945),
            (time(14, 45), AlertTrigger.SCHEDULED_1445),
        ):
            target = datetime.combine(
                started_at.date(),
                target_time,
                tzinfo=started_at.tzinfo,
            )
            if started_at <= target <= completed_at:
                return trigger
        return None

    @staticmethod
    def summary_due(now: datetime) -> bool:
        current = now.timetz().replace(tzinfo=None)
        return current >= time(15, 30)
