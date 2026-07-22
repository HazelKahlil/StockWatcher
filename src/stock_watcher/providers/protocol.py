from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from stock_watcher.domain import MarketEvent


class Provider(Protocol):
    """Normalized data boundary; implementations never expose vendor dictionaries."""

    name: str
    version: str

    def events(self) -> Iterator[MarketEvent]: ...
