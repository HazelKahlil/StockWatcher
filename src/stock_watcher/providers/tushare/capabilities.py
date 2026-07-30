from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from threading import Lock
from zoneinfo import ZoneInfo

from stock_watcher.config import HttpProfile, NativeRealtimeProfile

from .errors import ProviderError, ProviderFailureReason
from .models import Record, TransportResult
from .native_realtime_transport import NativeRealtimeTransport
from .pro_proxy_transport import ProProxyTransport
from .rate_limit import ApplicationRequestBudget
from .transport_protocol import TransportRequest

SHANGHAI = ZoneInfo("Asia/Shanghai")


class ProviderCapability(StrEnum):
    STOCK_LIST = "stock_list"
    TRADE_CALENDAR = "trade_calendar"
    SECTOR_CLASSIFICATION = "sector_classification"
    HISTORICAL_MINUTES = "historical_minutes"
    REALTIME_1 = "realtime_1"
    REALTIME_100 = "realtime_100"
    REALTIME_300 = "realtime_300"
    REALTIME_800 = "realtime_800"


class ProviderCapabilityState(StrEnum):
    UNKNOWN = "unknown"
    CHECKING = "checking"
    AVAILABLE = "available"
    RATE_LIMITED = "rate_limited"
    PERMISSION_DENIED = "permission_denied"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


CAPABILITY_ORDER: tuple[ProviderCapability, ...] = (
    ProviderCapability.STOCK_LIST,
    ProviderCapability.TRADE_CALENDAR,
    ProviderCapability.SECTOR_CLASSIFICATION,
    ProviderCapability.HISTORICAL_MINUTES,
    ProviderCapability.REALTIME_1,
    ProviderCapability.REALTIME_100,
    ProviderCapability.REALTIME_300,
    ProviderCapability.REALTIME_800,
)

REALTIME_CAPABILITIES = frozenset(
    {
        ProviderCapability.REALTIME_1,
        ProviderCapability.REALTIME_100,
        ProviderCapability.REALTIME_300,
        ProviderCapability.REALTIME_800,
    }
)


@dataclass(frozen=True, slots=True)
class ProviderCapabilityStatus:
    capability: ProviderCapability
    state: ProviderCapabilityState = ProviderCapabilityState.UNKNOWN
    checked_at: datetime | None = None
    last_success_at: datetime | None = None
    safe_reason: str | None = None
    next_retry_at: datetime | None = None
    record_count: int = 0
    elapsed_seconds: float | None = None

    @property
    def display_text(self) -> str:
        labels = {
            ProviderCapabilityState.UNKNOWN: "等待检测",
            ProviderCapabilityState.CHECKING: "检测中",
            ProviderCapabilityState.AVAILABLE: "正常",
            ProviderCapabilityState.RATE_LIMITED: "接口暂时限流，等待恢复",
            ProviderCapabilityState.PERMISSION_DENIED: "权限暂不可用",
            ProviderCapabilityState.UNAVAILABLE: "暂时不可用",
            ProviderCapabilityState.STALE: "数据时间不完整",
        }
        return labels[self.state]


class CapabilityCheckCoordinator:
    """Runs independent, serial capability checks after a Token is saved.

    A failed optional capability never invalidates the Token.  In particular,
    a 429 marks only the current check as rate-limited, asks the shared budget
    to hold all product-route starts, and later resumes from that check rather
    than replaying the completed checks.
    """

    def __init__(
        self,
        pro: ProProxyTransport,
        realtime: NativeRealtimeTransport,
        *,
        request_budget: ApplicationRequestBudget,
        clock: Callable[[], datetime] | None = None,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._pro = pro
        self._realtime = realtime
        self._request_budget = request_budget
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="stockwatcher-capability",
        )
        self._owns_executor = executor is None
        self._lock = Lock()
        self._in_flight = False
        self._future: Future[None] | None = None
        self._resume_from: ProviderCapability | None = None
        self._sample_codes: tuple[str, ...] = ()
        self._statuses = {
            capability: ProviderCapabilityStatus(capability)
            for capability in CAPABILITY_ORDER
        }

    @classmethod
    def for_profiles(
        cls,
        profile: HttpProfile,
        realtime_profile: NativeRealtimeProfile,
        secret_getter: Callable[[], str | None],
        *,
        request_budget: ApplicationRequestBudget,
        clock: Callable[[], datetime] | None = None,
    ) -> CapabilityCheckCoordinator:
        return cls(
            ProProxyTransport(
                profile,
                secret_getter,
                request_budget=request_budget,
            ),
            NativeRealtimeTransport(
                realtime_profile,
                secret_getter,
                request_budget=request_budget,
            ),
            request_budget=request_budget,
            clock=clock,
        )

    def reset(self) -> None:
        """Forget prior capability observations after a Token replacement."""
        with self._lock:
            self._resume_from = None
            self._sample_codes = ()
            self._statuses = {
                capability: ProviderCapabilityStatus(capability)
                for capability in CAPABILITY_ORDER
            }

    @property
    def in_flight(self) -> bool:
        with self._lock:
            return self._in_flight

    def statuses(self) -> dict[ProviderCapability, ProviderCapabilityStatus]:
        with self._lock:
            return dict(self._statuses)

    def status(self, capability: ProviderCapability) -> ProviderCapabilityStatus:
        with self._lock:
            return self._statuses[capability]

    def start_background(self) -> bool:
        """Schedule one serial pass if a due check exists and none is running."""
        with self._lock:
            # Do not call ``_next_due_locked`` here: selecting a due retry
            # consumes ``_resume_from``.  The worker must be the only caller
            # that advances the cursor, otherwise a background wake-up after
            # a 429 would skip the failed capability and move to a later one.
            if self._in_flight or not self._has_due_check_locked():
                return False
            self._in_flight = True
            self._future = self._executor.submit(self._run_loop)
            return True

    def run_until_blocked(self) -> None:
        """Synchronously execute due checks; intended for deterministic tests."""
        with self._lock:
            if self._in_flight:
                return
            self._in_flight = True
        self._run_loop()

    def _run_loop(self) -> None:
        while True:
            with self._lock:
                capability = self._next_due_locked()
                if capability is None:
                    self._in_flight = False
                    return
                previous = self._statuses[capability]
                self._statuses[capability] = ProviderCapabilityStatus(
                    capability=capability,
                    state=ProviderCapabilityState.CHECKING,
                    checked_at=_shanghai(self._clock()),
                    last_success_at=previous.last_success_at,
                    record_count=previous.record_count,
                )
            state = self._check(capability)
            if state is ProviderCapabilityState.RATE_LIMITED:
                with self._lock:
                    self._in_flight = False
                return

    def retry_now(self) -> bool:
        """Explicitly retry non-rate-limited checks without disturbing successes."""
        with self._lock:
            for capability, status in self._statuses.items():
                if status.state in {
                    ProviderCapabilityState.UNAVAILABLE,
                    ProviderCapabilityState.PERMISSION_DENIED,
                    ProviderCapabilityState.STALE,
                }:
                    self._statuses[capability] = ProviderCapabilityStatus(
                        capability,
                        last_success_at=status.last_success_at,
                    )
        return self.start_background()

    def shutdown(self) -> None:
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _next_due_locked(self) -> ProviderCapability | None:
        now = _shanghai(self._clock())
        if self._resume_from is not None:
            status = self._statuses[self._resume_from]
            if status.next_retry_at is not None and status.next_retry_at > now:
                return None
            capability = self._resume_from
            self._resume_from = None
            return capability
        for capability in CAPABILITY_ORDER:
            if self._statuses[capability].state is ProviderCapabilityState.UNKNOWN:
                return capability
        return None

    def _has_due_check_locked(self) -> bool:
        """Return whether a pass can start without advancing its cursor."""
        now = _shanghai(self._clock())
        if self._resume_from is not None:
            status = self._statuses[self._resume_from]
            return status.next_retry_at is None or status.next_retry_at <= now
        return any(
            status.state is ProviderCapabilityState.UNKNOWN
            for status in self._statuses.values()
        )

    def _check(self, capability: ProviderCapability) -> ProviderCapabilityState:
        now = _shanghai(self._clock())
        try:
            result = self._execute(capability)
        except ProviderError as error:
            return self._record_failure(capability, now, error)
        except Exception:
            return self._record_failure(
                capability,
                now,
                ProviderError(ProviderFailureReason.BUSINESS_ERROR),
            )
        return self._record_result(capability, now, result)

    def _execute(self, capability: ProviderCapability) -> TransportResult:
        if capability is ProviderCapability.STOCK_LIST:
            return self._pro.execute(
                TransportRequest(
                    endpoint="/",
                    api_name="stock_basic",
                    params={"list_status": "L"},
                    fields=("ts_code", "name", "market", "list_date"),
                )
            )
        if capability is ProviderCapability.TRADE_CALENDAR:
            now = _shanghai(self._clock())
            start = now - timedelta(days=7)
            return self._pro.execute(
                TransportRequest(
                    endpoint="/",
                    api_name="trade_cal",
                    params={
                        "exchange": "SSE",
                        "start_date": start.strftime("%Y%m%d"),
                        "end_date": now.strftime("%Y%m%d"),
                    },
                    fields=("exchange", "cal_date", "is_open"),
                    allow_empty=True,
                )
            )
        if capability is ProviderCapability.SECTOR_CLASSIFICATION:
            return self._pro.execute(
                TransportRequest(
                    endpoint="/",
                    api_name="index_classify",
                    params={"level": "L1", "src": "SW2021"},
                    fields=("index_code", "industry_name", "level"),
                    allow_empty=True,
                )
            )
        if capability is ProviderCapability.HISTORICAL_MINUTES:
            now = _shanghai(self._clock())
            start = now - timedelta(days=7)
            return self._pro.execute(
                TransportRequest(
                    endpoint="/",
                    api_name="stk_mins",
                    params={
                        "ts_code": self._sample_codes[0] if self._sample_codes else "000001.SZ",
                        "freq": "1min",
                        "start_date": start.strftime("%Y-%m-%d 09:30:00"),
                        "end_date": now.strftime("%Y-%m-%d 15:00:00"),
                    },
                    allow_empty=True,
                )
            )
        if capability in REALTIME_CAPABILITIES:
            count = {
                ProviderCapability.REALTIME_1: 1,
                ProviderCapability.REALTIME_100: 100,
                ProviderCapability.REALTIME_300: 300,
                ProviderCapability.REALTIME_800: 800,
            }[capability]
            codes = self._realtime_codes(count)
            return self._realtime.execute(
                TransportRequest(
                    endpoint="tushare.realtime_quote:sina",
                    api_name="realtime_quote",
                    params={"ts_code": ",".join(codes)},
                    fields=("ts_code", "price", "pre_close", "vol", "amount", "source_ts"),
                    realtime=True,
                    method="SDK",
                )
            )
        raise AssertionError(f"unsupported capability: {capability}")

    def _realtime_codes(self, count: int) -> tuple[str, ...]:
        if count == 1:
            return self._sample_codes[:1] or ("000001.SZ",)
        if len(self._sample_codes) < count:
            raise ProviderError(ProviderFailureReason.EMPTY_DATA)
        return self._sample_codes[:count]

    def _record_result(
        self,
        capability: ProviderCapability,
        now: datetime,
        result: TransportResult,
    ) -> ProviderCapabilityState:
        if capability is ProviderCapability.STOCK_LIST:
            self._sample_codes = _codes_from_records(result.records)
        expected = _expected_count(capability)
        state = (
            ProviderCapabilityState.STALE
            if capability in REALTIME_CAPABILITIES
            and result.provenance.source_ts is None
            else ProviderCapabilityState.AVAILABLE
            if len(result.records) >= expected
            else ProviderCapabilityState.UNAVAILABLE
        )
        with self._lock:
            old = self._statuses[capability]
            self._statuses[capability] = ProviderCapabilityStatus(
                capability=capability,
                state=state,
                checked_at=now,
                last_success_at=(
                    now
                    if state is ProviderCapabilityState.AVAILABLE
                    else old.last_success_at
                ),
                record_count=len(result.records),
                elapsed_seconds=result.elapsed_seconds,
            )
        return state

    def _record_failure(
        self,
        capability: ProviderCapability,
        now: datetime,
        error: ProviderError,
    ) -> ProviderCapabilityState:
        if error.reason is ProviderFailureReason.RATE_LIMITED:
            # HTTP and SDK transports set the shared cooldown at the point
            # where they observe a 429.  Reusing that remaining duration
            # avoids extending a supplier Retry-After every time this
            # coordinator translates the exception into UI state.  Test and
            # alternate transports may not have a budget, so establish it
            # here when needed.
            retry_after = self._request_budget.cooldown_remaining()
            if retry_after <= 0:
                retry_after = self._request_budget.pause_for(error.retry_after_seconds)
            state = ProviderCapabilityState.RATE_LIMITED
            next_retry_at = now + timedelta(seconds=retry_after)
        elif error.reason is ProviderFailureReason.PERMISSION_DENIED:
            state = ProviderCapabilityState.PERMISSION_DENIED
            next_retry_at = None
        else:
            state = ProviderCapabilityState.UNAVAILABLE
            next_retry_at = None
        with self._lock:
            old = self._statuses[capability]
            self._statuses[capability] = ProviderCapabilityStatus(
                capability=capability,
                state=state,
                checked_at=now,
                last_success_at=old.last_success_at,
                safe_reason=error.reason.value,
                next_retry_at=next_retry_at,
                record_count=old.record_count,
            )
            if state is ProviderCapabilityState.RATE_LIMITED:
                self._resume_from = capability
        return state


def aggregate_capability_status(
    statuses: Iterable[ProviderCapabilityStatus],
) -> ProviderCapabilityStatus:
    """Return the user-facing worst state while preserving the latest detail."""
    items = tuple(statuses)
    if not items:
        raise ValueError("at least one capability status is required")
    precedence = {
        ProviderCapabilityState.RATE_LIMITED: 6,
        ProviderCapabilityState.CHECKING: 5,
        ProviderCapabilityState.PERMISSION_DENIED: 4,
        ProviderCapabilityState.UNAVAILABLE: 3,
        ProviderCapabilityState.STALE: 2,
        ProviderCapabilityState.UNKNOWN: 1,
        ProviderCapabilityState.AVAILABLE: 0,
    }
    return max(items, key=lambda item: precedence[item.state])


def _expected_count(capability: ProviderCapability) -> int:
    return {
        ProviderCapability.REALTIME_1: 1,
        ProviderCapability.REALTIME_100: 100,
        ProviderCapability.REALTIME_300: 300,
        ProviderCapability.REALTIME_800: 800,
    }.get(capability, 1)


def _codes_from_records(records: tuple[Record, ...]) -> tuple[str, ...]:
    codes: list[str] = []
    for record in records:
        value = record.get("ts_code")
        if not isinstance(value, str):
            continue
        code = value.strip().upper()
        if len(code) == 9 and code[:6].isdigit() and code[6:] in {".SH", ".SZ", ".BJ"}:
            codes.append(code)
    return tuple(dict.fromkeys(codes))


def _shanghai(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)
