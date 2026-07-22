from __future__ import annotations

from collections.abc import Iterable, Iterator

from stock_watcher.domain import MarketEvent


class MockProvider:
    name = "mock"
    version = "v0.1"

    def __init__(self, events: Iterable[MarketEvent]) -> None:
        self._events = tuple(events)

    def events(self) -> Iterator[MarketEvent]:
        yield from self._events
