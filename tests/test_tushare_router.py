from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from stock_watcher.config import DataSourceMode
from stock_watcher.providers.tushare.cache import CacheEntry, MemoryDataCache
from stock_watcher.providers.tushare.capability_router import Capability, CapabilityRouter
from stock_watcher.providers.tushare.health import DataGateState, ProviderHealthGate
from stock_watcher.providers.tushare.models import (
    DataQuality,
    ProviderProvenance,
    SourceTimestampKind,
    TransportResult,
)
from stock_watcher.providers.tushare.transport_protocol import TransportRequest


@dataclass
class StubTransport:
    profile_name: str
    version: str = "test"

    def execute(self, request: TransportRequest) -> TransportResult:
        raise AssertionError(f"unexpected execute: {request}")


def router(mode: DataSourceMode = DataSourceMode.SUPER) -> CapabilityRouter:
    return CapabilityRouter(StubTransport("super"), StubTransport("fast"), mode=mode)


def test_default_and_realtime_routes_use_super() -> None:
    selected = router()
    assert selected.select(Capability.STOCK_LIST).profile_name == "super"
    assert selected.select(Capability.REALTIME_SNAPSHOT).profile_name == "super"
    assert not selected.allows_realtime_fallback()


def test_fast_requires_explicit_comparison_m0_allowlist() -> None:
    selected = router(DataSourceMode.FAST)
    with pytest.raises(RuntimeError, match="comparison M0"):
        selected.select(Capability.DAILY)
    selected.mark_fast_verified(Capability.DAILY)
    assert selected.select(Capability.DAILY).profile_name == "fast"


def test_fast_can_never_be_marked_for_realtime_or_level2() -> None:
    selected = router()
    with pytest.raises(ValueError, match="not eligible"):
        selected.mark_fast_verified(Capability.REALTIME_SNAPSHOT)
    with pytest.raises(ValueError, match="not eligible"):
        selected.mark_fast_verified(Capability.LEVEL2_EXPERIMENT)


def result(profile: str, second: int, quality: DataQuality) -> TransportResult:
    source = datetime(2026, 7, 29, 10, 0, second, tzinfo=ZoneInfo("Asia/Shanghai"))
    received = source.replace(microsecond=100_000)
    return TransportResult(
        records=({"ok": True},),
        http_status=200,
        elapsed_seconds=0.1,
        provenance=ProviderProvenance(
            provider_profile=profile,
            endpoint="/rt",
            provider_version="test",
            schema_version="v1",
            source_ts=source if quality is DataQuality.HEALTHY else None,
            received_ts=received,
            source_timestamp_kind=(
                SourceTimestampKind.SUPPLIER
                if quality is DataQuality.HEALTHY
                else SourceTimestampKind.MISSING
            ),
            freshness_seconds=0.1 if quality is DataQuality.HEALTHY else None,
            quality=quality,
            degraded=quality is not DataQuality.HEALTHY,
            fields_used=(),
        ),
    )


def test_provider_switch_closes_gate_and_requires_three_fresh_cycles() -> None:
    gate = ProviderHealthGate(required_warmup_cycles=3)
    assert gate.observe(result("super", 1, DataQuality.HEALTHY)) is DataGateState.WARMING
    assert gate.observe(result("super", 2, DataQuality.HEALTHY)) is DataGateState.WARMING
    assert gate.observe(result("super", 3, DataQuality.HEALTHY)) is DataGateState.OPEN
    assert gate.observe(result("fast", 4, DataQuality.HEALTHY)) is DataGateState.WARMING
    assert gate.fresh_cycles == 1


def test_missing_source_timestamp_never_opens_gate() -> None:
    gate = ProviderHealthGate(required_warmup_cycles=3)
    for second in range(1, 6):
        assert gate.observe(result("super", second, DataQuality.DEGRADED)) is DataGateState.WARMING
    assert gate.fresh_cycles == 0


def test_cache_rejects_source_time_rollback_and_clears_on_switch() -> None:
    cache = MemoryDataCache()
    first = result("super", 2, DataQuality.HEALTHY).provenance
    cache.put(
        "market",
        CacheEntry(({"ok": True},), first.source_ts, first.received_ts, "super"),
    )
    older = result("super", 1, DataQuality.HEALTHY).provenance
    with pytest.raises(ValueError, match="rollback"):
        cache.put(
            "market",
            CacheEntry(({"ok": True},), older.source_ts, older.received_ts, "super"),
        )
    cache.clear()
    assert cache.get("market") is None
