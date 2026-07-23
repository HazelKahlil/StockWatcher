from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum

from .candidates import CandidateBatch


@dataclass(frozen=True, slots=True)
class AlertPolicyConfig:
    cooldown: timedelta = timedelta(minutes=5)
    daily_limit: int = 3
    replacement_margin: float = 1.0
    replacement_cycles: int = 2


class AlertTrigger(StrEnum):
    """The explicitly classified source of an alert evaluation."""

    SCHEDULED_0945 = "scheduled-09:45"
    SCHEDULED_1450 = "scheduled-14:50"
    INTRADAY = "intraday"


@dataclass(frozen=True, slots=True)
class AlertDecision:
    should_alert: bool
    reason: str


@dataclass(slots=True)
class AlertPolicy:
    config: AlertPolicyConfig = field(default_factory=AlertPolicyConfig)
    _last_codes: tuple[str, ...] = ()
    _last_sent: dict[str, datetime] = field(default_factory=dict)
    _intraday_sent_today: int = 0
    _day: date | None = None
    _replacement_streak: int = 0
    _replacement_relation: tuple[str, str] | None = None
    _last_processed_source_ts: datetime | None = None

    def decide(self, batch: CandidateBatch, now: datetime, trigger: AlertTrigger) -> AlertDecision:
        if self._day != now.date():
            self._day, self._intraday_sent_today = now.date(), 0
            self._reset_replacement_debounce()
            self._last_sent = {}
            self._last_codes = ()
        if (
            trigger is AlertTrigger.INTRADAY
            and self._intraday_sent_today >= self.config.daily_limit
        ):
            return AlertDecision(False, "daily-limit")
        if (
            self._last_processed_source_ts is not None
            and batch.source_ts <= self._last_processed_source_ts
        ):
            return AlertDecision(False, "stale-source")
        self._last_processed_source_ts = batch.source_ts
        codes = tuple(candidate.code for candidate in batch.candidates)
        if not codes:
            return AlertDecision(False, "empty")
        if codes == self._last_codes:
            return AlertDecision(False, "unchanged")
        if any(
            now - sent < self.config.cooldown
            for code, sent in self._last_sent.items()
            if code in codes
        ):
            return AlertDecision(False, "cooldown")
        relation = self._replacement_relation_for(batch)
        if relation is not None:
            if relation == self._replacement_relation:
                self._replacement_streak += 1
            else:
                self._replacement_relation = relation
                self._replacement_streak = 1
            if self._replacement_streak < self.config.replacement_cycles:
                return AlertDecision(False, "replacement-debounce")
        else:
            self._reset_replacement_debounce()
        self._last_codes = codes
        self._last_sent.update({code: now for code in codes})
        if trigger is AlertTrigger.INTRADAY:
            self._intraday_sent_today += 1
        self._reset_replacement_debounce()
        return AlertDecision(True, "changed")

    def _replacement_relation_for(self, batch: CandidateBatch) -> tuple[str, str] | None:
        """Return a single near-margin top-three replacement, if this is one."""
        if not self._last_codes:
            return None
        previous = set(self._last_codes[:3])
        current = {candidate.code for candidate in batch.candidates[:3]}
        outgoing = previous - current
        entering = current - previous
        if len(outgoing) != 1 or len(entering) != 1:
            return None
        entering_candidate = next(
            candidate for candidate in batch.candidates if candidate.code in entering
        )
        third = batch.candidates[min(2, len(batch.candidates) - 1)]
        if entering_candidate.score - third.score >= self.config.replacement_margin:
            return None
        return next(iter(outgoing)), next(iter(entering))

    def _reset_replacement_debounce(self) -> None:
        self._replacement_streak = 0
        self._replacement_relation = None
