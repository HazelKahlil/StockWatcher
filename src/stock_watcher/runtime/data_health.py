from __future__ import annotations

from dataclasses import dataclass

from stock_watcher.domain import HealthState

from .scan_coordinator import MarketScan


@dataclass(frozen=True, slots=True)
class DataHealthConfig:
    fresh_seconds: float = 60.0
    stop_seconds: float = 120.0
    recovery_cycles: int = 3


class DataHealthTracker:
    """Fail-closed data-health state with a three-fresh-round recovery gate."""

    def __init__(self, config: DataHealthConfig = DataHealthConfig()) -> None:
        self.config = config
        self.state = HealthState.WARMING
        self.fresh_cycles = 0
        self.last_success: MarketScan | None = None

    def observe(self, scan: MarketScan) -> HealthState:
        if not scan.complete:
            return self.fail()
        if scan.max_source_age_seconds > self.config.stop_seconds:
            return self.fail()
        if scan.max_source_age_seconds > self.config.fresh_seconds:
            self.state = HealthState.STALE
            self.fresh_cycles = 0
            return self.state
        self.last_success = scan
        self.fresh_cycles += 1
        self.state = (
            HealthState.HEALTHY
            if self.fresh_cycles >= self.config.recovery_cycles
            else HealthState.WARMING
        )
        return self.state

    def fail(self) -> HealthState:
        self.fresh_cycles = 0
        self.state = HealthState.STOPPED
        return self.state

    def reset_for_recovery(self) -> None:
        """Discard an old source baseline before collecting fresh recovery rounds."""
        self.fresh_cycles = 0
        self.last_success = None
        self.state = HealthState.WARMING
