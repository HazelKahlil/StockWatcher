from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


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
        if self.source_ts.tzinfo is None or self.received_ts.tzinfo is None:
            raise ValueError("snapshot timestamps must be timezone-aware")
        if self.price < 0:
            raise ValueError("price cannot be negative")


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    state: HealthState
    observed_at: datetime
    detail: str = ""

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("health timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class MarketEvent:
    snapshot: Snapshot | None
    health: ProviderHealth

    @property
    def is_candidate_safe(self) -> bool:
        return self.health.state is HealthState.HEALTHY and self.snapshot is not None
