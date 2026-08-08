from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from stock_watcher.domain import (
    DataQuality,
    FundPriceSyncState,
    FundSignalState,
    FundStatus,
)


class FundCapability(StrEnum):
    UNAVAILABLE = "unavailable"
    DAILY_ONLY = "daily_only"
    INTRADAY_VERIFIED = "intraday_verified"


@dataclass(frozen=True, slots=True)
class FundCapabilityResult:
    capability: FundCapability
    reason: str
    fields: tuple[str, ...] = ()


class FundEngine:
    """Conservative fund-signal adapter; daily moneyflow never becomes intraday."""

    _DAILY_FIELDS = {
        "buy_lg_amount",
        "sell_lg_amount",
        "buy_elg_amount",
        "sell_elg_amount",
    }

    def probe(self, records: tuple[Mapping[str, object], ...]) -> FundCapabilityResult:
        if not records:
            return FundCapabilityResult(FundCapability.UNAVAILABLE, "moneyflow无可用记录")
        fields = set().union(*(record.keys() for record in records))
        if not self._DAILY_FIELDS <= fields:
            return FundCapabilityResult(
                FundCapability.UNAVAILABLE,
                "缺少可验证的大单/超大单字段",
                tuple(sorted(fields)),
            )
        has_intraday_time = any(
            isinstance(record.get(field), str) and ":" in str(record.get(field))
            for record in records
            for field in ("source_ts", "trade_time", "time")
        )
        if not has_intraday_time:
            return FundCapabilityResult(
                FundCapability.DAILY_ONLY,
                "moneyflow仅有交易日粒度，只用于三日背景和收盘总结",
                tuple(sorted(self._DAILY_FIELDS)),
            )
        # A timestamp alone does not prove the supplier updates the values
        # intraday.  Promotion requires a separate repeated-observation probe.
        return FundCapabilityResult(
            FundCapability.UNAVAILABLE,
            "检测到时间字段，但尚未证明盘中数值持续更新",
            tuple(sorted(self._DAILY_FIELDS)),
        )

    @staticmethod
    def unconfirmed() -> FundStatus:
        return FundStatus(
            super_large_state=FundSignalState.UNCONFIRMED,
            large_state=FundSignalState.UNCONFIRMED,
            price_sync_state=FundPriceSyncState.UNCONFIRMED,
            quality=DataQuality.UNAVAILABLE,
        )
