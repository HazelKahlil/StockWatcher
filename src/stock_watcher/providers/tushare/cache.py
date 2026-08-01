from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .models import Record


@dataclass(frozen=True, slots=True)
class CacheEntry:
    records: tuple[Record, ...]
    source_ts: datetime | None
    received_ts: datetime
    provider_profile: str


@dataclass(slots=True)
class MemoryDataCache:
    """Process-local cache. Provider switches clear it before warmup."""

    _entries: dict[str, CacheEntry] = field(default_factory=dict)

    def put(self, key: str, entry: CacheEntry) -> None:
        if not key:
            raise ValueError("cache key must not be empty")
        previous = self._entries.get(key)
        if (
            previous is not None
            and previous.source_ts is not None
            and entry.source_ts is not None
            and entry.source_ts < previous.source_ts
        ):
            raise ValueError("source timestamp rollback")
        self._entries[key] = entry

    def get(self, key: str) -> CacheEntry | None:
        return self._entries.get(key)

    def clear(self) -> None:
        self._entries.clear()
