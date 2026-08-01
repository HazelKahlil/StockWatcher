from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from .candidates import Candidate, CandidateBatch


@dataclass(frozen=True, slots=True)
class StableTop3Config:
    immediate_score_margin: float = 8.0
    confirmation_cycles: int = 3
    minimum_seat_hold_seconds: float = 45.0
    required_lead_cycles: int | None = None

    @property
    def lead_cycles(self) -> int:
        return max(1, self.required_lead_cycles or self.confirmation_cycles)


class StableTop3Selector:
    """Prevents small third/fourth-place score changes from shaking the UI."""

    _LEVEL = {"近": 0, "中": 1, "强": 2}

    def __init__(self, config: StableTop3Config = StableTop3Config()) -> None:
        self.config = config
        self.current: tuple[Candidate, ...] = ()
        self._pending_signature: tuple[str, ...] = ()
        self._pending_cycles = 0
        self._current_since: datetime | None = None

    def reset(self) -> None:
        self.current = ()
        self._pending_signature = ()
        self._pending_cycles = 0
        self._current_since = None

    def update(
        self,
        raw: CandidateBatch,
        *,
        current_candidates: Mapping[str, Candidate] | None = None,
        now: datetime | None = None,
        force: bool = False,
    ) -> CandidateBatch:
        hold_enabled = now is not None
        observed_at = now or raw.generated_at or raw.source_ts
        if not self.current:
            self.current = raw.candidates
            self._current_since = observed_at
            return raw
        if current_candidates is not None:
            current_codes = tuple(candidate.code for candidate in self.current)
            if any(code not in current_candidates for code in current_codes):
                # Stability may delay a rank replacement, but it must never
                # preserve a security that is absent from the current fresh,
                # eligible full-market snapshot.
                self.current = raw.candidates
                self._clear_pending()
                self._current_since = observed_at
                return raw
            self.current = tuple(current_candidates[code] for code in current_codes)
        raw_signature = tuple(candidate.code for candidate in raw.candidates)
        current_signature = tuple(candidate.code for candidate in self.current)
        if raw_signature == current_signature:
            self.current = raw.candidates
            self._clear_pending()
            return raw
        if force or self._requires_immediate_replacement(raw.candidates):
            self.current = raw.candidates
            self._clear_pending()
            self._current_since = observed_at
            return raw
        if raw_signature == self._pending_signature:
            self._pending_cycles += 1
        else:
            self._pending_signature = raw_signature
            self._pending_cycles = 1
        if (
            self._pending_cycles >= self.config.lead_cycles
            and self._seat_hold_elapsed(observed_at, enabled=hold_enabled)
        ):
            self.current = raw.candidates
            self._clear_pending()
            self._current_since = observed_at
            return raw
        latest_by_code = dict(current_candidates or {})
        # Selected raw rows carry the final formal/supplement classification,
        # so they take precedence over the pre-selection current pool.
        latest_by_code.update(
            {candidate.code: candidate for candidate in raw.candidates}
        )
        refreshed = tuple(
            latest_by_code.get(candidate.code, candidate) for candidate in self.current
        )
        self.current = refreshed
        formal_count = sum(candidate.is_formal for candidate in refreshed)
        return CandidateBatch(
            source_ts=raw.source_ts,
            generated_at=raw.generated_at,
            candidates=refreshed,
            health=raw.health,
            overall_weak=formal_count < 3,
            fund_module=raw.fund_module,
            formal_count=formal_count,
        )

    def _seat_hold_elapsed(self, observed_at: datetime, *, enabled: bool) -> bool:
        if not enabled or self._current_since is None:
            return True
        return observed_at - self._current_since >= timedelta(
            seconds=max(0.0, self.config.minimum_seat_hold_seconds)
        )

    def _requires_immediate_replacement(
        self,
        incoming: tuple[Candidate, ...],
    ) -> bool:
        current_codes = {candidate.code for candidate in self.current}
        incoming_codes = {candidate.code for candidate in incoming}
        entering = [candidate for candidate in incoming if candidate.code not in current_codes]
        leaving = [candidate for candidate in self.current if candidate.code not in incoming_codes]
        if not entering or not leaving:
            return True
        weakest_leaving = min(
            leaving,
            key=lambda candidate: (
                self._LEVEL.get(candidate.level, 0),
                candidate.total_score,
            ),
        )
        return any(
            self._LEVEL.get(candidate.level, 0)
            > self._LEVEL.get(weakest_leaving.level, 0)
            or candidate.total_score - weakest_leaving.total_score
            >= self.config.immediate_score_margin
            for candidate in entering
        )

    def _clear_pending(self) -> None:
        self._pending_signature = ()
        self._pending_cycles = 0
