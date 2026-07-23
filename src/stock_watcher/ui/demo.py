from __future__ import annotations

from datetime import datetime, timedelta

from stock_watcher.domain import SHANGHAI, CandidateInput, HealthState, ProviderHealth, Security
from stock_watcher.engine import CandidateBatch, CandidateConfig, CandidateEngine

DEMO_CONFIG = CandidateConfig(version="v0.2-mac-alpha", app_version="0.2.0")
DEMO_PROVIDER_VERSION = "synthetic-v0.2"


def demo_batch(source_ts: datetime) -> CandidateBatch:
    """Build the fixed, reviewable HEALTHY scene used by the Mac demo."""
    values = (
        ("600001", "模拟智造", 18.62, 6.20, 2.30, "模拟工业", 3.20, 1.10),
        ("600002", "回放材料", 12.08, 4.10, 1.50, "模拟材料", 2.60, 0.60),
        ("600003", "观察能源", 9.86, 3.20, 1.00, "弱板块", 0.80, 0.20),
        ("600004", "样本消费", 15.20, 1.60, 0.70, "弱板块", 0.60, -0.10),
    )
    inputs = tuple(
        CandidateInput(
            security=Security(code, name, "SH"),
            price=price,
            change_pct=change,
            velocity_pct=velocity,
            sector=sector,
            sector_strength=sector_strength,
            trend_3d_pct=trend,
            source_ts=source_ts,
            received_ts=source_ts,
            provider_version=DEMO_PROVIDER_VERSION,
            config_version=DEMO_CONFIG.version,
        )
        for code, name, price, change, velocity, sector, sector_strength, trend in values
    )
    result = CandidateEngine().calculate(inputs, HealthState.HEALTHY, DEMO_CONFIG)
    if result is None:
        raise RuntimeError("demo batch unexpectedly failed health gate")
    return result


def demo_health(state: HealthState, source_ts: datetime, detail: str) -> ProviderHealth:
    return ProviderHealth(
        state=state,
        source_ts=source_ts,
        received_ts=source_ts,
        provider_version=DEMO_PROVIDER_VERSION,
        config_version=DEMO_CONFIG.version,
        detail=detail,
    )


def demo_clock() -> datetime:
    return datetime(2026, 7, 23, 9, 45, tzinfo=SHANGHAI)


def recovery_clock() -> datetime:
    return demo_clock() + timedelta(minutes=1)
