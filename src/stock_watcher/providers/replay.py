from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from stock_watcher.domain import HealthState, MarketEvent


class ReplayProvider:
    """Replays normalized events while enforcing safe disconnect and recovery semantics."""

    name = "replay"
    version = "v0.1"

    def __init__(self, events: tuple[MarketEvent, ...], min_fresh_samples: int = 3) -> None:
        if min_fresh_samples < 1:
            raise ValueError("min_fresh_samples must be at least one")
        self._events = events
        self._min_fresh_samples = min_fresh_samples

    def events(self) -> Iterator[MarketEvent]:
        seen: set[tuple[str, object]] = set()
        reconnecting = False
        fresh_warming_samples = 0
        last_warming_source_ts: datetime | None = None
        for event in self._events:
            if event.health.state is HealthState.STOPPED:
                # A STOPPED transition is a safety event, not market data. It must always
                # survive code/source_ts deduplication and retain its own provenance.
                reconnecting = True
                fresh_warming_samples = 0
                last_warming_source_ts = None
                yield MarketEvent(snapshot=None, health=event.health)
                continue

            if event.snapshot is None:
                yield event
                continue
            key = (event.snapshot.security.code, event.snapshot.source_ts)
            if key in seen:
                continue

            if reconnecting:
                if event.health.state is HealthState.WARMING:
                    seen.add(key)
                    fresh_warming_samples += 1
                    last_warming_source_ts = event.snapshot.source_ts
                    yield event
                    continue
                if (
                    event.health.state is not HealthState.HEALTHY
                    or fresh_warming_samples < self._min_fresh_samples
                    or last_warming_source_ts is None
                    or event.snapshot.source_ts <= last_warming_source_ts
                ):
                    # Never let a post-disconnect sample become candidate-safe before
                    # enough strictly new WARMING samples have been observed.
                    continue
                reconnecting = False

            seen.add(key)
            yield event
