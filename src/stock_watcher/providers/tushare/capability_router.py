from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from stock_watcher.config import DataSourceMode

from .transport_protocol import TushareTransport


class Capability(StrEnum):
    HEALTH = "health"
    STATUS = "status"
    CATALOG = "catalog"
    STOCK_LIST = "stock_list"
    TRADE_CALENDAR = "trade_calendar"
    DAILY = "daily"
    REALTIME_SNAPSHOT = "realtime_snapshot"
    REALTIME_MINUTES = "realtime_minutes"
    HISTORICAL_MINUTES = "historical_minutes"
    SECTOR_CLASSIFY = "sector_classify"
    SECTOR_COMPONENTS = "sector_components"
    LEVEL2_EXPERIMENT = "level2_experiment"


_FAST_ELIGIBLE = {
    Capability.STOCK_LIST,
    Capability.TRADE_CALENDAR,
    Capability.DAILY,
}


@dataclass(slots=True)
class CapabilityRouter:
    super_transport: TushareTransport
    fast_transport: TushareTransport
    mode: DataSourceMode = DataSourceMode.SUPER
    verified_fast_capabilities: set[Capability] = field(default_factory=set)

    def mark_fast_verified(self, capability: Capability) -> None:
        if capability not in _FAST_ELIGIBLE:
            raise ValueError("fast transport is not eligible for this capability")
        self.verified_fast_capabilities.add(capability)

    def select(self, capability: Capability) -> TushareTransport:
        if self.mode is DataSourceMode.FAST:
            if capability not in self.verified_fast_capabilities:
                raise RuntimeError("fast capability has not passed comparison M0")
            return self.fast_transport
        if (
            self.mode is DataSourceMode.SMART
            and capability in _FAST_ELIGIBLE
            and capability in self.verified_fast_capabilities
        ):
            return self.fast_transport
        return self.super_transport

    def allows_realtime_fallback(self) -> bool:
        return False
