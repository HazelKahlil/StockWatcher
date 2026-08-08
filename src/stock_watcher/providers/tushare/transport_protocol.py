from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .models import TransportResult


@dataclass(frozen=True, slots=True)
class TransportRequest:
    endpoint: str
    api_name: str | None = None
    params: dict[str, str | int | float | bool] = field(default_factory=dict)
    fields: tuple[str, ...] = ()
    method: str = "POST"
    realtime: bool = False
    allow_empty: bool = False


class TushareTransport(Protocol):
    profile_name: str
    version: str

    def execute(self, request: TransportRequest) -> TransportResult: ...
