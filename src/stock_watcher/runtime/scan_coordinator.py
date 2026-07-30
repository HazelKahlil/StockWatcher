from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from stock_watcher.domain import (
    SHANGHAI,
    DataQuality,
    RealtimeQuote,
    Security,
)
from stock_watcher.providers.tushare.models import TransportResult
from stock_watcher.providers.tushare.transport_protocol import TransportRequest


class RealtimeTransport(Protocol):
    def execute(self, request: TransportRequest) -> TransportResult: ...


class ScanInProgressError(RuntimeError):
    pass


class IncompleteScanError(RuntimeError):
    pass


class ScanCancelledError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MarketScan:
    scan_id: str
    started_at: datetime
    completed_at: datetime
    quotes: tuple[RealtimeQuote, ...]
    requested_count: int
    coverage_ratio: float
    duplicate_count: int
    source_span_seconds: float
    max_source_age_seconds: float
    elapsed_seconds: float

    @property
    def complete(self) -> bool:
        return (
            self.requested_count > 0
            and self.coverage_ratio >= 0.99
            and self.duplicate_count == 0
        )


class FullMarketScanCoordinator:
    """Forms one atomic full-market snapshot and prevents overlapping scans."""

    def __init__(
        self,
        transport: RealtimeTransport,
        *,
        clock: Callable[[], datetime] | None = None,
        minimum_coverage_ratio: float = 0.99,
        max_source_span_seconds: float = 60.0,
        max_future_skew_seconds: float = 10.0,
    ) -> None:
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self.minimum_coverage_ratio = minimum_coverage_ratio
        self.max_source_span_seconds = max_source_span_seconds
        self.max_future_skew_seconds = max_future_skew_seconds
        self._scan_lock = threading.Lock()
        self._cancellation_lock = threading.Lock()
        self._cancellation_generation = 0

    def cancel_current_scan(self) -> None:
        """Prevent an in-flight response from becoming a valid market snapshot."""
        with self._cancellation_lock:
            self._cancellation_generation += 1

    def fetch_once(self, securities: tuple[Security, ...]) -> MarketScan:
        if not securities:
            raise ValueError("full-market scan requires at least one security")
        if not self._scan_lock.acquire(blocking=False):
            raise ScanInProgressError("a full-market scan is already running")
        try:
            with self._cancellation_lock:
                generation = self._cancellation_generation
            return self._fetch_locked(securities, generation)
        finally:
            self._scan_lock.release()

    def _fetch_locked(
        self,
        securities: tuple[Security, ...],
        cancellation_generation: int,
    ) -> MarketScan:
        self._raise_if_cancelled(cancellation_generation)
        requested = {security.code: security for security in securities}
        if len(requested) != len(securities):
            raise ValueError("security universe contains duplicate codes")
        scan_id = uuid.uuid4().hex
        started = _shanghai(self._clock())
        result = self._transport.execute(
            TransportRequest(
                endpoint="tushare.realtime_quote:sina",
                api_name="realtime_quote",
                params={"ts_code": ",".join(requested)},
                fields=(
                    "ts_code",
                    "name",
                    "open",
                    "pre_close",
                    "price",
                    "high",
                    "low",
                    "vol",
                    "amount",
                    "source_ts",
                    "received_ts",
                ),
                realtime=True,
                method="SDK",
            )
        )
        self._raise_if_cancelled(cancellation_generation)
        completed = _shanghai(self._clock())
        seen: set[str] = set()
        duplicates = 0
        quotes: list[RealtimeQuote] = []
        for record in result.records:
            code = _text(record.get("ts_code"))
            if code not in requested:
                continue
            if code in seen:
                duplicates += 1
                continue
            seen.add(code)
            quotes.append(_quote(record, requested[code], scan_id, result))
        coverage = len(seen) / len(requested)
        if duplicates:
            raise IncompleteScanError("supplier returned duplicate security codes")
        if coverage < self.minimum_coverage_ratio:
            raise IncompleteScanError(
                f"full-market coverage {coverage:.4f} is below minimum "
                f"{self.minimum_coverage_ratio:.4f}"
            )
        source_times = [quote.source_ts for quote in quotes]
        source_span = (
            (max(source_times) - min(source_times)).total_seconds()
            if source_times
            else float("inf")
        )
        if source_span > self.max_source_span_seconds:
            raise IncompleteScanError("full-market source timestamp span is too wide")
        if any(
            (source_ts - completed).total_seconds() > self.max_future_skew_seconds
            for source_ts in source_times
        ):
            raise IncompleteScanError("supplier timestamp is unexpectedly in the future")
        max_age = max(
            (max(0.0, (completed - quote.source_ts).total_seconds()) for quote in quotes),
            default=float("inf"),
        )
        return MarketScan(
            scan_id=scan_id,
            started_at=started,
            completed_at=completed,
            quotes=tuple(sorted(quotes, key=lambda quote: quote.security.code)),
            requested_count=len(requested),
            coverage_ratio=coverage,
            duplicate_count=duplicates,
            source_span_seconds=source_span,
            max_source_age_seconds=max_age,
            elapsed_seconds=result.elapsed_seconds,
        )

    def _raise_if_cancelled(self, generation: int) -> None:
        with self._cancellation_lock:
            if generation != self._cancellation_generation:
                raise ScanCancelledError("full-market scan was cancelled during recovery")


def _quote(
    record: dict[str, str | int | float | bool | None],
    security: Security,
    scan_id: str,
    result: TransportResult,
) -> RealtimeQuote:
    source_ts = _timestamp(record.get("source_ts"))
    received_ts = _timestamp(record.get("received_ts"))
    quality_name = _text(record.get("data_quality"))
    quality = (
        DataQuality.GOOD
        if quality_name == "HEALTHY"
        else DataQuality.DEGRADED
        if quality_name == "DEGRADED"
        else DataQuality.UNAVAILABLE
    )
    return RealtimeQuote(
        security=security,
        price=_number(record, "price"),
        previous_close=_number(record, "pre_close"),
        open=_number(record, "open"),
        high=_number(record, "high"),
        low=_number(record, "low"),
        volume_shares=_number(record, "vol"),
        amount_cny=_number(record, "amount"),
        source_ts=source_ts,
        received_ts=received_ts,
        scan_id=scan_id,
        provider_version=result.provenance.provider_version,
        quality=quality,
        trading_state="trading" if _number(record, "price") > 0 else "suspended",
    )


def _number(
    record: dict[str, str | int | float | bool | None],
    field: str,
) -> float:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise IncompleteScanError(f"realtime field is missing: {field}")
    try:
        parsed = float(value)
    except ValueError as error:
        raise IncompleteScanError(f"realtime field is invalid: {field}") from error
    if parsed < 0:
        raise IncompleteScanError(f"realtime field is negative: {field}")
    return parsed


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise IncompleteScanError("realtime timestamp is missing")
    try:
        return _shanghai(datetime.fromisoformat(value))
    except ValueError as error:
        raise IncompleteScanError("realtime timestamp is invalid") from error


def _shanghai(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)
