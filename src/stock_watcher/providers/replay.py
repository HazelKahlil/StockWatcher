from __future__ import annotations

from collections.abc import Iterator

from stock_watcher.domain import HealthState, MarketEvent


class ReplayProvider:
    """Replays a fixed sequence and discards duplicate code/source-time pairs."""

    name = "replay"
    version = "v0.1"

    def __init__(self, events: tuple[MarketEvent, ...]) -> None:
        self._events = events

    def events(self) -> Iterator[MarketEvent]:
        seen: set[tuple[str, object]] = set()
        for event in self._events:
            if event.snapshot is None:
                yield event
                continue
            key = (event.snapshot.security.code, event.snapshot.source_ts)
            if key in seen:
                continue
            seen.add(key)
            if event.health.state is HealthState.STOPPED:
                yield MarketEvent(snapshot=None, health=event.health)
            else:
                yield event
