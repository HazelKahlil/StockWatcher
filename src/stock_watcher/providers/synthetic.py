from __future__ import annotations

from datetime import datetime, timedelta
from random import Random

from stock_watcher.domain import HealthState, MarketEvent, ProviderHealth, Security, Snapshot


class SyntheticScenarioBuilder:
    """Creates deterministic, explicitly-labelled simulated events for tests and demos."""

    def __init__(self, now: datetime, seed: int = 7) -> None:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
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
        health = ProviderHealth(HealthState.HEALTHY, self.now, "simulated duplicate")
        self._events.append(MarketEvent(previous, health))
        return self

    def reconnect(self) -> SyntheticScenarioBuilder:
        self.stopped()
        return self.normal()

    def build(self) -> tuple[MarketEvent, ...]:
        return tuple(self._events)

    def _append(self, state: HealthState, price: float) -> SyntheticScenarioBuilder:
        index = len(self._events)
        moment = self.now + timedelta(seconds=index)
        health = ProviderHealth(state, moment, f"simulated {state.lower()}")
        snapshot = Snapshot(self._security, price, moment, moment, "synthetic-v0.1", "v0.1")
        self._events.append(MarketEvent(snapshot, health))
        return self
