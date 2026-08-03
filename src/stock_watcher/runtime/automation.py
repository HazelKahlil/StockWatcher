from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from stock_watcher.domain import SHANGHAI


class AutomationTaskType(StrEnum):
    FIXED_0945 = "scheduled-09:45"
    FIXED_1445 = "scheduled-14:45"
    SUMMARY_1530 = "summary-15:30"


class AutomationTaskState(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AutomationTaskSpec:
    task_key: str
    task_type: AutomationTaskType
    trade_date: date
    target_at: datetime
    deadline_at: datetime


@dataclass(frozen=True, slots=True)
class AutomationScheduleConfig:
    fixed_deadline_seconds: int = 90
    summary_deadline_hours: int = 8


class AutomationPlanner:
    """Creates deterministic daily tasks from an injected Asia/Shanghai clock.

    The planner intentionally does not know whether data are available.  It only
    records that a product obligation became due.  Execution and retry decisions
    live in the desktop session so a cache refresh, rate limit or wake event can
    never silently erase the 09:45 / 14:45 / 15:30 obligation.
    """

    _fixed = (
        (time(9, 45), AutomationTaskType.FIXED_0945),
        (time(14, 45), AutomationTaskType.FIXED_1445),
    )
    _summary = (time(15, 30), AutomationTaskType.SUMMARY_1530)

    def __init__(self, config: AutomationScheduleConfig = AutomationScheduleConfig()) -> None:
        self.config = config

    def due(self, now: datetime) -> tuple[AutomationTaskSpec, ...]:
        current = _shanghai(now)
        return tuple(
            spec
            for spec in self.for_date(current.date())
            if spec.target_at <= current <= spec.deadline_at
        )

    def for_date(self, trade_date: date) -> tuple[AutomationTaskSpec, ...]:
        """Return every product obligation for one local date.

        Persisting the full set before each target arrives makes a later missed
        deadline auditable.  A task can no longer disappear simply because a
        cache refresh or a sleeping computer prevented the timer callback from
        entering the old scan path.
        """
        output: list[AutomationTaskSpec] = []
        for target_time, task_type in self._fixed:
            target = datetime.combine(trade_date, target_time, tzinfo=SHANGHAI)
            output.append(
                self._spec(
                    task_type,
                    target,
                    target + timedelta(seconds=self.config.fixed_deadline_seconds),
                )
            )
        summary_time, summary_type = self._summary
        summary_target = datetime.combine(trade_date, summary_time, tzinfo=SHANGHAI)
        output.append(
            self._spec(
                summary_type,
                summary_target,
                summary_target + timedelta(hours=self.config.summary_deadline_hours),
            )
        )
        return tuple(output)

    @staticmethod
    def task_key(task_type: AutomationTaskType, trade_date: date) -> str:
        return f"{trade_date.isoformat()}:{task_type.value}"

    def _spec(
        self,
        task_type: AutomationTaskType,
        target_at: datetime,
        deadline_at: datetime,
    ) -> AutomationTaskSpec:
        return AutomationTaskSpec(
            task_key=self.task_key(task_type, target_at.date()),
            task_type=task_type,
            trade_date=target_at.date(),
            target_at=target_at,
            deadline_at=deadline_at,
        )


def _shanghai(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)
