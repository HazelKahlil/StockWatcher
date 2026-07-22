from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _require_shanghai(timestamp: datetime, field_name: str) -> None:
    if timestamp.tzinfo is None or getattr(timestamp.tzinfo, "key", None) != SHANGHAI.key:
        raise ValueError(f"{field_name} must use the Asia/Shanghai timezone")


class HealthState(StrEnum):
    WARMING = "WARMING"
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class Security:
    code: str
    name: str
    market: str


@dataclass(frozen=True, slots=True)
class Snapshot:
    security: Security
    price: float
    source_ts: datetime
    received_ts: datetime
    provider_version: str
    config_version: str

    def __post_init__(self) -> None:
        _require_shanghai(self.source_ts, "snapshot source_ts")
        _require_shanghai(self.received_ts, "snapshot received_ts")
        if self.price < 0:
            raise ValueError("price cannot be negative")


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    state: HealthState
    source_ts: datetime
    received_ts: datetime
    provider_version: str
    config_version: str
    detail: str = ""

    def __post_init__(self) -> None:
        _require_shanghai(self.source_ts, "health source_ts")
        _require_shanghai(self.received_ts, "health received_ts")

    @property
    def observed_at(self) -> datetime:
        """Compatibility alias; storage must use source and received timestamps."""
        return self.received_ts


@dataclass(frozen=True, slots=True)
class MarketEvent:
    snapshot: Snapshot | None
    health: ProviderHealth

    @property
    def is_candidate_safe(self) -> bool:
        return self.health.state is HealthState.HEALTHY and self.snapshot is not None
