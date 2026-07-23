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


@dataclass(frozen=True, slots=True)
class CandidateInput:
    """Normalized, point-in-time inputs for the deterministic candidate engine.

    This deliberately contains no provider payload or fund-line fields.  The fund
    module remains unavailable until the separate M0 data gate is passed.
    """

    security: Security
    price: float
    change_pct: float
    velocity_pct: float
    sector: str
    sector_strength: float
    trend_3d_pct: float
    source_ts: datetime
    received_ts: datetime
    provider_version: str
    config_version: str
    is_st: bool = False
    is_delisting: bool = False
    is_suspended: bool = False
    is_limit_up: bool = False
    is_new_or_corporate_action: bool = False
    is_complete: bool = True

    def __post_init__(self) -> None:
        _require_shanghai(self.source_ts, "candidate input source_ts")
        _require_shanghai(self.received_ts, "candidate input received_ts")
        if self.price < 0:
            raise ValueError("candidate input price cannot be negative")

    @property
    def exclusion_reason(self) -> str | None:
        if self.security.market == "BJ":
            return "北交所"
        if self.is_st or self.security.name.upper().startswith(("ST", "*ST")):
            return "ST"
        if self.is_delisting:
            return "退市整理"
        if self.is_suspended:
            return "停牌"
        if self.is_limit_up:
            return "一字涨停"
        if self.is_new_or_corporate_action:
            return "新股/复牌/除权当日"
        if not self.is_complete:
            return "数据不完整"
        return None
