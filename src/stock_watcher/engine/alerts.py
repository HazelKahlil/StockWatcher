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
    SCHEDULED_0945 = "scheduled-09:45"
    SCHEDULED_1445 = "scheduled-14:45"
    # Compatibility symbol for saved v0.2 traces; it now resolves to the
    # Human Owner-confirmed 14:45 trigger and is not shown in the ordinary UI.
    SCHEDULED_1450 = "scheduled-14:45"
    INTRADAY = "intraday"


@dataclass(frozen=True, slots=True)
class AlertDecision:
    should_alert: bool
    reason: str


@dataclass(frozen=True, slots=True)
class StrongMovementEvent:
    triggering_codes: tuple[str, ...]
    strength: float
    funds_unconfirmed: bool


@dataclass(slots=True)
class StrongMovementDetector:
    minimum_velocity_1m: float = 0.8
    minimum_velocity_increase: float = 0.35
    minimum_sector_score: float = 18.0
    minimum_sector_increase: float = 0.1
    _last_velocity: dict[str, float] = field(default_factory=dict)
    _last_sector_score: dict[str, float] = field(default_factory=dict)
    _last_source_ts: datetime | None = None

    def reset(self) -> None:
        """Forget pre-disconnect baselines so recovery cannot emit old events."""
        self._last_velocity = {}
        self._last_sector_score = {}
        self._last_source_ts = None

    def evaluate(self, batch: CandidateBatch) -> StrongMovementEvent | None:
        if self._last_source_ts is not None and batch.source_ts <= self._last_source_ts:
            return None
        self._last_source_ts = batch.source_ts
        triggering: list[str] = []
        strengths: list[float] = []
        funds_unconfirmed = False
        for candidate in batch.candidates:
            previous_velocity = self._last_velocity.get(candidate.code)
            previous_sector = self._last_sector_score.get(candidate.code)
            self._last_velocity[candidate.code] = candidate.velocity_pct
            self._last_sector_score[candidate.code] = candidate.sector_score
            if not candidate.is_formal:
                continue
            if previous_velocity is None or previous_sector is None:
                continue
            velocity_increase = (
                candidate.velocity_pct - previous_velocity
                if previous_velocity is not None
                else candidate.acceleration_pct
            )
            sector_increase = (
                candidate.sector_score - previous_sector
                if previous_sector is not None
                else 0.0
            )
            if (
                candidate.velocity_pct < self.minimum_velocity_1m
                or velocity_increase is None
                or velocity_increase < self.minimum_velocity_increase
                or candidate.sector_score < self.minimum_sector_score
                or sector_increase < self.minimum_sector_increase
            ):
                continue
            fund_is_unconfirmed = (
                candidate.super_large_state == "unconfirmed"
                and candidate.large_state == "unconfirmed"
            )
            fund_supports = (
                candidate.super_large_state == "enhancing"
                or candidate.large_state == "enhancing"
            ) and candidate.fund_sync_state != "diverging"
            if not fund_is_unconfirmed and not fund_supports:
                continue
            funds_unconfirmed = funds_unconfirmed or fund_is_unconfirmed
            triggering.append(candidate.code)
            strengths.append(velocity_increase + max(0.0, sector_increase))
        if not triggering:
            return None
        return StrongMovementEvent(
            triggering_codes=tuple(triggering),
            strength=max(strengths),
            funds_unconfirmed=funds_unconfirmed,
        )


@dataclass(slots=True)
class AlertPolicy:
    config: AlertPolicyConfig = field(default_factory=AlertPolicyConfig)
    _last_codes: tuple[str, ...] = ()
    _last_sent: dict[str, datetime] = field(default_factory=dict)
    _intraday_sent_today: int = 0
    _fixed_sent: set[AlertTrigger] = field(default_factory=set)
    _day: date | None = None
    _replacement_streak: int = 0
    _replacement_relation: tuple[str, str] | None = None
    _last_processed_source_ts: datetime | None = None
    _last_event_strength: float = 0.0

    def decide(
        self,
        batch: CandidateBatch,
        now: datetime,
        trigger: AlertTrigger,
        *,
        strong_movement: bool = True,
        triggering_codes: tuple[str, ...] = (),
        event_strength: float = 0.0,
    ) -> AlertDecision:
        if self._day != now.date():
            self._reset_day(now.date())
        codes = tuple(candidate.code for candidate in batch.candidates)
        if len(codes) != 3:
            return AlertDecision(False, "requires-three")
        if trigger in {AlertTrigger.SCHEDULED_0945, AlertTrigger.SCHEDULED_1445}:
            if trigger in self._fixed_sent:
                return AlertDecision(False, "fixed-already-sent")
            if (
                self._last_processed_source_ts is not None
                and batch.source_ts < self._last_processed_source_ts
            ):
                return AlertDecision(False, "stale-source")
            self._last_processed_source_ts = batch.source_ts
            self._fixed_sent.add(trigger)
            self._last_codes = codes
            return AlertDecision(True, trigger.value)
        if not strong_movement:
            return AlertDecision(False, "not-strong-movement")
        if self._intraday_sent_today >= self.config.daily_limit:
            return AlertDecision(False, "daily-limit")
        if self._replacement_relation is not None and codes == self._last_codes:
            self._reset_replacement_debounce(clear_source_ts=True)
        if (
            self._last_processed_source_ts is not None
            and batch.source_ts <= self._last_processed_source_ts
        ):
            return AlertDecision(False, "stale-source")
        self._last_processed_source_ts = batch.source_ts
        if codes == self._last_codes and event_strength <= self._last_event_strength:
            return AlertDecision(False, "unchanged")
        cooldown_codes = triggering_codes or codes
        if any(
            now - sent < self.config.cooldown
            for code, sent in self._last_sent.items()
            if code in cooldown_codes
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
        self._last_sent.update({code: now for code in cooldown_codes})
        self._intraday_sent_today += 1
        self._last_event_strength = event_strength
        self._reset_replacement_debounce()
        return AlertDecision(True, "strong-movement")

    def _replacement_relation_for(self, batch: CandidateBatch) -> tuple[str, str] | None:
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

    def _reset_day(self, day: date) -> None:
        self._day = day
        self._intraday_sent_today = 0
        self._fixed_sent = set()
        self._last_sent = {}
        self._last_codes = ()
        self._last_event_strength = 0.0
        self._reset_replacement_debounce(clear_source_ts=True)

    def _reset_replacement_debounce(self, *, clear_source_ts: bool = False) -> None:
        self._replacement_streak = 0
        self._replacement_relation = None
        if clear_source_ts:
            self._last_processed_source_ts = None
