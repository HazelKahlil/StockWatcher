from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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


class DataQuality(StrEnum):
    GOOD = "GOOD"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class SourceTimestampKind(StrEnum):
    PROVIDER = "provider"
    RECEIVED_FALLBACK = "received_fallback"


class FundSignalState(StrEnum):
    ENHANCING = "enhancing"
    NEUTRAL = "neutral"
    WEAK = "weak"
    UNCONFIRMED = "unconfirmed"


class FundPriceSyncState(StrEnum):
    SYNCHRONIZED = "synchronized"
    DIVERGING = "diverging"
    UNCONFIRMED = "unconfirmed"


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
    previous_close: float | None = None
    volume: float | None = None
    amount: float | None = None
    trading_state: str = "unknown"
    quality: DataQuality = DataQuality.GOOD
    source_timestamp_kind: SourceTimestampKind = SourceTimestampKind.PROVIDER

    def __post_init__(self) -> None:
        _require_shanghai(self.source_ts, "snapshot source_ts")
        _require_shanghai(self.received_ts, "snapshot received_ts")
        if self.price < 0:
            raise ValueError("price cannot be negative")
        for name, value in (
            ("previous_close", self.previous_close),
            ("volume", self.volume),
            ("amount", self.amount),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")


@dataclass(frozen=True, slots=True)
class RealtimeQuote:
    security: Security
    price: float
    previous_close: float
    open: float
    high: float
    low: float
    volume_shares: float
    amount_cny: float
    source_ts: datetime
    received_ts: datetime
    scan_id: str
    provider_version: str
    quality: DataQuality = DataQuality.GOOD
    trading_state: str = "trading"

    def __post_init__(self) -> None:
        _require_shanghai(self.source_ts, "realtime quote source_ts")
        _require_shanghai(self.received_ts, "realtime quote received_ts")
        if not self.scan_id:
            raise ValueError("realtime quote scan_id must not be empty")
        if min(
            self.price,
            self.previous_close,
            self.open,
            self.high,
            self.low,
            self.volume_shares,
            self.amount_cny,
        ) < 0:
            raise ValueError("realtime quote values cannot be negative")


@dataclass(frozen=True, slots=True)
class RollingFeatures:
    code: str
    source_ts: datetime
    change_pct: float
    velocity_1m_pct: float | None
    velocity_3m_pct: float | None
    velocity_5m_pct: float | None
    acceleration_pct: float | None
    volume_delta_1m: float | None
    amount_delta_1m: float | None
    volume_ratio_1m: float | None
    amount_ratio_1m: float | None
    intraday_high_break: bool
    high_3d_break: bool
    market_relative_strength: float | None

    def __post_init__(self) -> None:
        _require_shanghai(self.source_ts, "rolling features source_ts")


@dataclass(frozen=True, slots=True)
class SectorMetrics:
    sector_code: str
    sector_name: str
    sector_type: str
    member_count: int
    valid_count: int
    up_count: int
    up_ratio: float
    median_change_pct: float
    median_velocity_1m: float
    strong_count: int
    amount_growth: float | None
    score: float
    gate_passed: bool
    source_ts: datetime

    def __post_init__(self) -> None:
        _require_shanghai(self.source_ts, "sector metrics source_ts")
        if min(self.member_count, self.valid_count, self.up_count, self.strong_count) < 0:
            raise ValueError("sector counts cannot be negative")
        if not 0 <= self.up_ratio <= 1:
            raise ValueError("sector up_ratio must be between zero and one")


@dataclass(frozen=True, slots=True)
class FundStatus:
    super_large_state: FundSignalState = FundSignalState.UNCONFIRMED
    large_state: FundSignalState = FundSignalState.UNCONFIRMED
    price_sync_state: FundPriceSyncState = FundPriceSyncState.UNCONFIRMED
    super_large_value: float | None = None
    large_value: float | None = None
    value_unit: str | None = None
    source_ts: datetime | None = None
    quality: DataQuality = DataQuality.UNAVAILABLE

    def __post_init__(self) -> None:
        if self.source_ts is not None:
            _require_shanghai(self.source_ts, "fund status source_ts")


@dataclass(frozen=True, slots=True)
class HistoricalBar:
    security: Security
    period: str
    source_ts: datetime
    received_ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    provider_version: str
    config_version: str
    quality: DataQuality = DataQuality.GOOD

    def __post_init__(self) -> None:
        _require_shanghai(self.source_ts, "bar source_ts")
        _require_shanghai(self.received_ts, "bar received_ts")
        if min(self.open, self.high, self.low, self.close, self.volume, self.amount) < 0:
            raise ValueError("bar values cannot be negative")


@dataclass(frozen=True, slots=True)
class SectorMembership:
    """A stock-to-sector relation observed from the official provider.

    TdxQuant does not publish a relation effective timestamp in ``get_relation``.
    ``effective_date`` therefore records the local observation date and the
    received-fallback timestamp kind keeps that limitation machine-readable.
    """

    security: Security
    sector_code: str
    sector_name: str
    sector_type: str
    member_count: int
    effective_date: date
    source_ts: datetime
    received_ts: datetime
    provider_version: str
    config_version: str
    quality: DataQuality = DataQuality.DEGRADED
    source_timestamp_kind: SourceTimestampKind = SourceTimestampKind.RECEIVED_FALLBACK

    def __post_init__(self) -> None:
        _require_shanghai(self.source_ts, "sector membership source_ts")
        _require_shanghai(self.received_ts, "sector membership received_ts")
        if self.member_count < 0:
            raise ValueError("sector member_count cannot be negative")


@dataclass(frozen=True, slots=True)
class TradingDate:
    """An A-share open date returned by the official TdxQuant calendar.

    The API returns open-date strings without a provider generation timestamp,
    so source time explicitly falls back to the local receipt time.
    """

    market: str
    trading_date: date
    is_open: bool
    source_ts: datetime
    received_ts: datetime
    provider_version: str
    config_version: str
    quality: DataQuality = DataQuality.DEGRADED
    source_timestamp_kind: SourceTimestampKind = SourceTimestampKind.RECEIVED_FALLBACK

    def __post_init__(self) -> None:
        _require_shanghai(self.source_ts, "trading date source_ts")
        _require_shanghai(self.received_ts, "trading date received_ts")


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
    velocity_1m_pct: float | None = None
    velocity_3m_pct: float | None = None
    velocity_5m_pct: float | None = None
    acceleration_pct: float | None = None
    volume_ratio_1m: float | None = None
    amount_ratio_1m: float | None = None
    intraday_high_break: bool = False
    high_3d_break: bool = False
    market_relative_strength: float | None = None
    sector_code: str = ""
    sector_type: str = "industry"
    sector_gate_passed: bool | None = None
    sector_up_ratio: float | None = None
    sector_strong_count: int | None = None
    sector_rank_percentile: float | None = None
    sector_relative_strength: float | None = None
    sector_median_change_pct: float | None = None
    sector_rank: int | None = None
    sector_valid_count: int | None = None
    highs_rising_3d: bool = False
    lows_rising_3d: bool = False
    amount_rising_3d: bool = False
    fund_status: FundStatus = FundStatus()
    data_completeness: float = 1.0

    def __post_init__(self) -> None:
        _require_shanghai(self.source_ts, "candidate input source_ts")
        _require_shanghai(self.received_ts, "candidate input received_ts")
        if self.price < 0:
            raise ValueError("candidate input price cannot be negative")
        if not 0 <= self.data_completeness <= 1:
            raise ValueError("candidate input data_completeness must be between zero and one")

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
