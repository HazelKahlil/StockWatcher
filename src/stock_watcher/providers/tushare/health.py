from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import DataQuality, TransportResult


class DataGateState(StrEnum):
    WARMING = "WARMING"
    OPEN = "OPEN"
    STOPPED = "STOPPED"


@dataclass(slots=True)
class ProviderHealthGate:
    required_warmup_cycles: int = 3
    state: DataGateState = DataGateState.WARMING
    fresh_cycles: int = 0
    last_profile: str | None = None

    def provider_switched(self, profile: str) -> None:
        self.last_profile = profile
        self.state = DataGateState.WARMING
        self.fresh_cycles = 0

    def failure(self) -> None:
        self.state = DataGateState.STOPPED
        self.fresh_cycles = 0

    def observe(self, result: TransportResult) -> DataGateState:
        if result.provenance.provider_profile != self.last_profile:
            self.provider_switched(result.provenance.provider_profile)
        if (
            result.provenance.quality is not DataQuality.HEALTHY
            or result.provenance.source_ts is None
        ):
            self.state = DataGateState.WARMING
            self.fresh_cycles = 0
            return self.state
        self.fresh_cycles += 1
        self.state = (
            DataGateState.OPEN
            if self.fresh_cycles >= self.required_warmup_cycles
            else DataGateState.WARMING
        )
        return self.state
