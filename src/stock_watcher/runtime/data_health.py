from __future__ import annotations

from dataclasses import dataclass

from stock_watcher.domain import HealthState

from .scan_coordinator import MarketScan


@dataclass(frozen=True, slots=True)
class DataHealthConfig:
    fresh_seconds: float = 60.0
    stop_seconds: float = 120.0
    initial_cycles: int = 1
    recovery_cycles: int = 3


class DataHealthTracker:
    """One complete cold-start round; three fresh rounds after interruption."""

    def __init__(self, config: DataHealthConfig = DataHealthConfig()) -> None:
        self.config = config
        self.state = HealthState.WARMING
        self.fresh_cycles = 0
        self.last_success: MarketScan | None = None
        self._recovering = False

    @property
    def required_cycles(self) -> int:
        return (
            self.config.recovery_cycles
            if self._recovering
            else self.config.initial_cycles
        )

    def observe(self, scan: MarketScan) -> HealthState:
        if not scan.complete:
            return self.fail()
        if scan.max_source_age_seconds > self.config.stop_seconds:
            return self.fail()
        if scan.max_source_age_seconds > self.config.fresh_seconds:
            self.state = HealthState.STALE
            self.fresh_cycles = 0
            self._recovering = True
            return self.state
        self.last_success = scan
        self.fresh_cycles += 1
        self.state = (
            HealthState.HEALTHY
            if self.fresh_cycles >= self.required_cycles
            else HealthState.WARMING
        )
        if self.state is HealthState.HEALTHY:
            self._recovering = False
        return self.state

    def fail(self) -> HealthState:
        self.fresh_cycles = 0
        self._recovering = True
        self.state = HealthState.STOPPED
        return self.state

    def reset_for_initial(self) -> None:
        """Reset a true cold start without weakening later recovery gates."""
        self.fresh_cycles = 0
        self.last_success = None
        self._recovering = False
        self.state = HealthState.WARMING

    def reset_for_recovery(self) -> None:
        """Discard an old source baseline before collecting fresh recovery rounds."""
        self.fresh_cycles = 0
        self.last_success = None
        self._recovering = True
        self.state = HealthState.WARMING
