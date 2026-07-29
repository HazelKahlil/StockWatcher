from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

Scalar: TypeAlias = str | int | float | bool | None
Record: TypeAlias = dict[str, Scalar]


class DataQuality(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    STOPPED = "STOPPED"


class SourceTimestampKind(StrEnum):
    SUPPLIER = "supplier"
    MARKET_DATE_ONLY = "market_date_only"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class ProviderProvenance:
    provider_profile: str
    endpoint: str
    provider_version: str
    schema_version: str
    source_ts: datetime | None
    received_ts: datetime
    source_timestamp_kind: SourceTimestampKind
    freshness_seconds: float | None
    quality: DataQuality
    degraded: bool
    fields_used: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParsedPayload:
    records: tuple[Record, ...]
    count: int | None
    code: int
    message_present: bool


@dataclass(frozen=True, slots=True)
class TransportResult:
    records: tuple[Record, ...]
    http_status: int
    elapsed_seconds: float
    provenance: ProviderProvenance
