from __future__ import annotations

from datetime import datetime, timedelta
from random import Random

from stock_watcher.domain import (
    SHANGHAI,
    HealthState,
    MarketEvent,
    ProviderHealth,
    Security,
    Snapshot,
)


class SyntheticScenarioBuilder:
    """Creates deterministic, explicitly-labelled simulated events for tests and demos."""

    def __init__(self, now: datetime, seed: int = 7) -> None:
        if now.tzinfo is None or getattr(now.tzinfo, "key", None) != SHANGHAI.key:
            raise ValueError("now must use the Asia/Shanghai timezone")
        self.now = now
        self._random = Random(seed)
        self._events: list[MarketEvent] = []
        self._security = Security(code="600000", name="模拟样本", market="SH")

    def normal(self, price: float | None = None) -> SyntheticScenarioBuilder:
        value = price if price is not None else round(10 + self._random.random(), 2)
        return self._append(HealthState.HEALTHY, value)

    def stale(self) -> SyntheticScenarioBuilder:
        return self._append(HealthState.STALE, 10.0)

    def stopped(self) -> SyntheticScenarioBuilder:
        return self._append(HealthState.STOPPED, 10.0)

    def warming(self) -> SyntheticScenarioBuilder:
        return self._append(HealthState.WARMING, 10.0)

    def duplicate_timestamp(self) -> SyntheticScenarioBuilder:
        if not self._events or self._events[-1].snapshot is None:
            raise ValueError("a preceding snapshot is required")
        previous = self._events[-1].snapshot
        assert previous is not None
        health = ProviderHealth(
            HealthState.HEALTHY,
            previous.source_ts,
            previous.received_ts,
            previous.provider_version,
            previous.config_version,
            "simulated duplicate",
        )
        self._events.append(MarketEvent(previous, health))
        return self

    def reconnect(self) -> SyntheticScenarioBuilder:
        self.stopped()
        for _ in range(3):
            self.warming()
        return self.normal()

    def build(self) -> tuple[MarketEvent, ...]:
        return tuple(self._events)

    def _append(self, state: HealthState, price: float) -> SyntheticScenarioBuilder:
        index = len(self._events)
        moment = self.now + timedelta(seconds=index)
        provider_version = "synthetic-v0.1"
        config_version = "v0.1"
        health = ProviderHealth(
            state,
            moment,
            moment,
            provider_version,
            config_version,
            f"simulated {state.lower()}",
        )
        snapshot = Snapshot(self._security, price, moment, moment, provider_version, config_version)
        self._events.append(MarketEvent(snapshot, health))
        return self
