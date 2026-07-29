from __future__ import annotations

from dataclasses import dataclass

from .candidates import Candidate, CandidateBatch


@dataclass(frozen=True, slots=True)
class StableTop3Config:
    immediate_score_margin: float = 8.0
    confirmation_cycles: int = 3


class StableTop3Selector:
    """Prevents small third/fourth-place score changes from shaking the UI."""

    _LEVEL = {"近": 0, "中": 1, "强": 2}

    def __init__(self, config: StableTop3Config = StableTop3Config()) -> None:
        self.config = config
        self.current: tuple[Candidate, ...] = ()
        self._pending_signature: tuple[str, ...] = ()
        self._pending_cycles = 0

    def reset(self) -> None:
        self.current = ()
        self._pending_signature = ()
        self._pending_cycles = 0

    def update(self, raw: CandidateBatch) -> CandidateBatch:
        if not self.current:
            self.current = raw.candidates
            return raw
        raw_signature = tuple(candidate.code for candidate in raw.candidates)
        current_signature = tuple(candidate.code for candidate in self.current)
        if raw_signature == current_signature:
            self.current = raw.candidates
            self._clear_pending()
            return raw
        if self._requires_immediate_replacement(raw.candidates):
            self.current = raw.candidates
            self._clear_pending()
            return raw
        if raw_signature == self._pending_signature:
            self._pending_cycles += 1
        else:
            self._pending_signature = raw_signature
            self._pending_cycles = 1
        if self._pending_cycles >= self.config.confirmation_cycles:
            self.current = raw.candidates
            self._clear_pending()
            return raw
        latest_by_code = {candidate.code: candidate for candidate in raw.candidates}
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
