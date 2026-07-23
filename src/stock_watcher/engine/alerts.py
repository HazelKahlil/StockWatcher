from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .candidates import CandidateBatch


@dataclass(frozen=True, slots=True)
class AlertPolicyConfig:
    cooldown: timedelta = timedelta(minutes=5)
    daily_limit: int = 3
    replacement_margin: float = 1.0
    replacement_cycles: int = 2


@dataclass(frozen=True, slots=True)
class AlertDecision:
    should_alert: bool
    reason: str


@dataclass(slots=True)
class AlertPolicy:
    config: AlertPolicyConfig = field(default_factory=AlertPolicyConfig)
    _last_codes: tuple[str, ...] = ()
    _last_sent: dict[str, datetime] = field(default_factory=dict)
    _sent_today: int = 0
    _day: object | None = None
    _replacement_streak: int = 0

    def decide(self, batch: CandidateBatch, now: datetime) -> AlertDecision:
        if self._day != now.date():
            self._day, self._sent_today, self._replacement_streak = now.date(), 0, 0
            self._last_sent = {}
            self._last_codes = ()
        if self._sent_today >= self.config.daily_limit:
            return AlertDecision(False, "daily-limit")
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
        if self._last_codes and set(codes[:3]) != set(self._last_codes[:3]):
            entering = next(
                (
                    candidate
                    for candidate in batch.candidates
                    if candidate.code not in self._last_codes
                ),
                None,
            )
            third = batch.candidates[min(2, len(batch.candidates) - 1)]
            if (
                entering is not None
                and entering.score - third.score < self.config.replacement_margin
            ):
                self._replacement_streak += 1
                if self._replacement_streak < self.config.replacement_cycles:
                    return AlertDecision(False, "replacement-debounce")
        self._replacement_streak = 0
        self._last_codes = codes
        self._last_sent.update({code: now for code in codes})
        self._sent_today += 1
        return AlertDecision(True, "changed")
