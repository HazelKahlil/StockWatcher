from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import cast
from zoneinfo import ZoneInfo

import pytest
import requests

from stock_watcher.config import DataSourceSettings, HttpProfile, NativeRealtimeProfile
from stock_watcher.providers.tushare.capabilities import (
    CAPABILITY_ORDER,
    CapabilityCheckCoordinator,
    ProviderCapability,
    ProviderCapabilityState,
)
from stock_watcher.providers.tushare.errors import ProviderError, ProviderFailureReason
from stock_watcher.providers.tushare.fast_transport import FastTransport
from stock_watcher.providers.tushare.models import (
    DataQuality,
    ProviderProvenance,
    SourceTimestampKind,
    TransportResult,
)
from stock_watcher.providers.tushare.native_realtime_transport import NativeRealtimeTransport
from stock_watcher.providers.tushare.pro_proxy_transport import ProProxyTransport
from stock_watcher.providers.tushare.rate_limit import ApplicationRequestBudget
from stock_watcher.providers.tushare.sdk_pro_transport import TushareSdkProTransport
from stock_watcher.providers.tushare.transport_protocol import TransportRequest
from stock_watcher.security import PRIMARY_CREDENTIAL, MemoryCredentialStore
from stock_watcher.ui import data_source_status
from stock_watcher.ui.data_source_settings import DataSourceSettingsController
from stock_watcher.ui.data_source_status import CredentialTestResult, LightweightCredentialTester

SHANGHAI = ZoneInfo("Asia/Shanghai")


def fixed_now() -> datetime:
    return datetime(2026, 7, 30, 10, 0, tzinfo=SHANGHAI)


def code(index: int) -> str:
    return f"{index:06d}.SZ"


def result(
    records: tuple[dict[str, str | int | float | bool | None], ...],
    *,
    source_timestamp: bool = True,
) -> TransportResult:
    now = fixed_now()
    return TransportResult(
        records=records,
        http_status=200,
        elapsed_seconds=0.1,
        provenance=ProviderProvenance(
            provider_profile="test",
            endpoint="test",
            provider_version="test",
            schema_version="v1",
            source_ts=now if source_timestamp else None,
            received_ts=now,
            source_timestamp_kind=(
                SourceTimestampKind.SUPPLIER
                if source_timestamp
                else SourceTimestampKind.MISSING
            ),
            freshness_seconds=0.0 if source_timestamp else None,
            quality=DataQuality.HEALTHY,
            degraded=not source_timestamp,
            fields_used=(),
        ),
    )


def stock_list(count: int = 800) -> TransportResult:
    return result(tuple({"ts_code": code(index)} for index in range(1, count + 1)))


def realtime(count: int) -> TransportResult:
    return result(tuple({"ts_code": code(index)} for index in range(1, count + 1)))


@dataclass
class SequenceTransport:
    outcomes: list[TransportResult | Exception]
    calls: list[TransportRequest] = field(default_factory=list)

    def execute(self, request: TransportRequest) -> TransportResult:
        self.calls.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@dataclass
class ManualTime:
    value: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class FakeSession:
    def __init__(self, response: requests.Response) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []
        self.trust_env = False

    def request(self, method: str, url: str, **kwargs: object) -> requests.Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


def http_response(status: int, *, retry_after: str | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({"code": 0, "data": []}).encode("utf-8")
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    return response


def primary_profile() -> HttpProfile:
    return DataSourceSettings().primary_profile


def test_lightweight_primary_tester_uses_only_one_base_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[TransportRequest] = []

    class RecordingPro:
        def execute(self, request: TransportRequest) -> object:
            seen.append(request)
            return SimpleNamespace(http_status=200)

    monkeypatch.setattr(
        data_source_status,
        "TushareSdkProTransport",
        lambda *_args, **_kwargs: RecordingPro(),
    )

    outcome = LightweightCredentialTester().test(primary_profile(), "candidate-token")

    assert outcome.success
    assert [request.api_name for request in seen] == ["trade_cal"]
    assert "后台分项检测" in outcome.permission_summary


@pytest.mark.parametrize(
    ("retry_after", "expected"), [(None, 60.0), ("17", 17.0)])
def test_http_429_sets_pro_lane_cooldown_without_retrying(
    retry_after: str | None,
    expected: float,
) -> None:
    manual = ManualTime()
    budget = ApplicationRequestBudget(
        clock=manual.monotonic,
        sleeper=manual.sleep,
    )
    session = FakeSession(http_response(429, retry_after=retry_after))
    transport = FastTransport(
        primary_profile(),
        lambda: "test-token",
        session=cast(requests.Session, session),
        request_budget=budget,
    )

    with pytest.raises(ProviderError) as raised:
        transport.execute(TransportRequest(endpoint="/", api_name="trade_cal"))

    assert raised.value.reason is ProviderFailureReason.RATE_LIMITED
    assert raised.value.retry_after_seconds == expected
    assert len(session.calls) == 1
    assert budget.cooldown_remaining() == expected


def test_capability_checks_keep_token_after_sector_429_and_resume_from_failure() -> None:
    manual = ManualTime()
    wall_clock = [fixed_now()]
    budget = ApplicationRequestBudget(
        clock=manual.monotonic,
        sleeper=manual.sleep,
    )
    pro = SequenceTransport(
        [
            stock_list(),
            result(({"cal_date": "20260730"},)),
            ProviderError(ProviderFailureReason.RATE_LIMITED),
            result(({"index_code": "I1"},)),
            result(({"ts_code": code(1)},)),
        ]
    )
    native = SequenceTransport([realtime(1), realtime(100), realtime(300), realtime(800)])
    coordinator = CapabilityCheckCoordinator(
        cast(ProProxyTransport, pro),
        cast(NativeRealtimeTransport, native),
        request_budget=budget,
        clock=lambda: wall_clock[0],
    )
    store = MemoryCredentialStore()
    store.set(PRIMARY_CREDENTIAL, "old-token")

    class PassingTester:
        def test(self, _profile: object, _secret: str) -> CredentialTestResult:
            return CredentialTestResult(
                success=True,
                tested_at=fixed_now(),
                status_text="基础调用通过",
                permission_summary="后台检测",
                expires_at="未知",
            )

    controller = DataSourceSettingsController(
        store=store,
        tester=PassingTester(),
        capability_checks=coordinator,
    )
    controller.test_candidate(
        "primary",
        "new-token",
        base_url=str(primary_profile().base_url).rstrip("/"),
        use_system_proxy=False,
    )
    assert controller.commit_candidate("primary", confirmed=True)

    deadline = time.monotonic() + 1.0
    while coordinator.in_flight and time.monotonic() < deadline:
        time.sleep(0.005)
    assert store.get(PRIMARY_CREDENTIAL) == "new-token"
    assert (
        coordinator.status(ProviderCapability.SECTOR_CLASSIFICATION).state
        is ProviderCapabilityState.RATE_LIMITED
    )
    assert [request.api_name for request in pro.calls] == [
        "stock_basic",
        "trade_cal",
        "index_classify",
    ]
    assert (
        coordinator.status(ProviderCapability.REALTIME_800).state
        is ProviderCapabilityState.AVAILABLE
    )

    manual.value += 60.0
    wall_clock[0] += timedelta(seconds=60)
    # A background retry must resume the rate-limited sector check itself;
    # it must not advance to a later unknown capability first.
    assert coordinator.start_background()
    deadline = time.monotonic() + 1.0
    while coordinator.in_flight and time.monotonic() < deadline:
        time.sleep(0.005)

    assert store.get(PRIMARY_CREDENTIAL) == "new-token"
    assert (
        coordinator.status(ProviderCapability.SECTOR_CLASSIFICATION).state
        is ProviderCapabilityState.AVAILABLE
    )
    assert [request.api_name for request in pro.calls].count("stock_basic") == 1
    assert [request.api_name for request in pro.calls].count("trade_cal") == 1
    assert [request.api_name for request in pro.calls].count("index_classify") == 2
    assert (
        coordinator.status(ProviderCapability.REALTIME_800).state
        is ProviderCapabilityState.AVAILABLE
    )
    coordinator.shutdown()


def test_cached_codes_reopen_progressive_realtime_checks_after_pro_429() -> None:
    manual = ManualTime()
    budget = ApplicationRequestBudget(
        clock=manual.monotonic,
        sleeper=manual.sleep,
    )
    pro = SequenceTransport(
        [ProviderError(ProviderFailureReason.RATE_LIMITED)]
    )
    native = SequenceTransport(
        [realtime(1), realtime(100), realtime(300), realtime(800)]
    )
    coordinator = CapabilityCheckCoordinator(
        cast(ProProxyTransport, pro),
        cast(NativeRealtimeTransport, native),
        request_budget=budget,
        clock=fixed_now,
    )

    coordinator.run_until_blocked()

    assert (
        coordinator.status(ProviderCapability.STOCK_LIST).state
        is ProviderCapabilityState.RATE_LIMITED
    )
    assert (
        coordinator.status(ProviderCapability.REALTIME_1).state
        is ProviderCapabilityState.AVAILABLE
    )
    assert (
        coordinator.status(ProviderCapability.REALTIME_800).state
        is ProviderCapabilityState.UNAVAILABLE
    )
    assert len(native.calls) == 1

    coordinator.seed_realtime_codes(code(index) for index in range(1, 801))
    coordinator.run_until_blocked()

    assert (
        coordinator.status(ProviderCapability.STOCK_LIST).state
        is ProviderCapabilityState.RATE_LIMITED
    )
    assert all(
        coordinator.status(capability).state is ProviderCapabilityState.AVAILABLE
        for capability in (
            ProviderCapability.REALTIME_1,
            ProviderCapability.REALTIME_100,
            ProviderCapability.REALTIME_300,
            ProviderCapability.REALTIME_800,
        )
    )
    assert len(native.calls) == 4
    coordinator.shutdown()


def test_realtime_only_progression_never_calls_ordinary_pro() -> None:
    pro = SequenceTransport([])
    native = SequenceTransport(
        [realtime(1), realtime(100), realtime(300), realtime(800)]
    )
    coordinator = CapabilityCheckCoordinator(
        cast(ProProxyTransport, pro),
        cast(NativeRealtimeTransport, native),
        request_budget=ApplicationRequestBudget(),
        clock=fixed_now,
    )
    coordinator.seed_realtime_codes(code(index) for index in range(1, 801))

    coordinator.run_realtime_until_blocked()

    assert pro.calls == []
    assert all(
        coordinator.status(capability).state is ProviderCapabilityState.UNKNOWN
        for capability in (
            ProviderCapability.STOCK_LIST,
            ProviderCapability.TRADE_CALENDAR,
            ProviderCapability.SECTOR_CLASSIFICATION,
            ProviderCapability.HISTORICAL_MINUTES,
        )
    )
    assert all(
        coordinator.status(capability).state is ProviderCapabilityState.AVAILABLE
        for capability in (
            ProviderCapability.REALTIME_1,
            ProviderCapability.REALTIME_100,
            ProviderCapability.REALTIME_300,
            ProviderCapability.REALTIME_800,
        )
    )
    coordinator.shutdown()


def test_realtime_background_progression_never_calls_ordinary_pro() -> None:
    pro = SequenceTransport([])
    native = SequenceTransport(
        [realtime(1), realtime(100), realtime(300), realtime(800)]
    )
    coordinator = CapabilityCheckCoordinator(
        cast(ProProxyTransport, pro),
        cast(NativeRealtimeTransport, native),
        request_budget=ApplicationRequestBudget(),
        clock=fixed_now,
    )
    coordinator.seed_realtime_codes(code(index) for index in range(1, 801))

    assert coordinator.start_realtime_background()
    deadline = time.monotonic() + 2
    while coordinator.in_flight and time.monotonic() < deadline:
        time.sleep(0.01)

    assert not coordinator.in_flight
    assert pro.calls == []
    assert all(
        coordinator.status(capability).state is ProviderCapabilityState.AVAILABLE
        for capability in (
            ProviderCapability.REALTIME_1,
            ProviderCapability.REALTIME_100,
            ProviderCapability.REALTIME_300,
            ProviderCapability.REALTIME_800,
        )
    )
    coordinator.shutdown()


def test_pro_and_realtime_429_lanes_both_resume_without_skips_or_looping() -> None:
    manual = ManualTime()
    wall_clock = [fixed_now()]
    budget = ApplicationRequestBudget(
        clock=manual.monotonic,
        sleeper=manual.sleep,
    )
    pro = SequenceTransport(
        [
            ProviderError(ProviderFailureReason.RATE_LIMITED),
            stock_list(),
            result(({"cal_date": "20260730"},)),
            result(({"index_code": "I1"},)),
            result(({"ts_code": code(1)},)),
        ]
    )
    native = SequenceTransport(
        [
            ProviderError(ProviderFailureReason.RATE_LIMITED),
            realtime(1),
            realtime(100),
            realtime(300),
            realtime(800),
        ]
    )
    coordinator = CapabilityCheckCoordinator(
        cast(ProProxyTransport, pro),
        cast(NativeRealtimeTransport, native),
        request_budget=budget,
        clock=lambda: wall_clock[0],
    )
    coordinator.seed_realtime_codes(code(index) for index in range(1, 801))

    coordinator.run_until_blocked()

    assert not coordinator.in_flight
    assert (
        coordinator.status(ProviderCapability.STOCK_LIST).state
        is ProviderCapabilityState.RATE_LIMITED
    )
    assert (
        coordinator.status(ProviderCapability.REALTIME_1).state
        is ProviderCapabilityState.RATE_LIMITED
    )
    assert len(pro.calls) == 1
    assert len(native.calls) == 1

    manual.value += 60.0
    wall_clock[0] += timedelta(seconds=60)
    coordinator.run_until_blocked()

    assert not coordinator.in_flight
    assert all(
        coordinator.status(capability).state is ProviderCapabilityState.AVAILABLE
        for capability in CAPABILITY_ORDER
    )
    assert len(pro.calls) == 5
    assert len(native.calls) == 5
    coordinator.shutdown()


def test_history_empty_does_not_block_independent_realtime_capability_check() -> None:
    budget = ApplicationRequestBudget()
    pro = SequenceTransport(
        [
            stock_list(),
            result(({"cal_date": "20260730"},)),
            result(({"index_code": "I1"},)),
            result((), source_timestamp=False),
        ]
    )
    native = SequenceTransport([realtime(1), realtime(100), realtime(300), realtime(800)])
    coordinator = CapabilityCheckCoordinator(
        cast(ProProxyTransport, pro),
        cast(NativeRealtimeTransport, native),
        request_budget=budget,
        clock=fixed_now,
    )

    coordinator.run_until_blocked()

    assert (
        coordinator.status(ProviderCapability.HISTORICAL_MINUTES).state
        is ProviderCapabilityState.UNAVAILABLE
    )
    assert (
        coordinator.status(ProviderCapability.REALTIME_1).state
        is ProviderCapabilityState.AVAILABLE
    )
    assert (
        coordinator.status(ProviderCapability.REALTIME_800).state
        is ProviderCapabilityState.AVAILABLE
    )
    coordinator.shutdown()


def test_controller_rejects_a_second_inflight_lightweight_check() -> None:
    started = threading.Event()
    release = threading.Event()
    store = MemoryCredentialStore()
    store.set(PRIMARY_CREDENTIAL, "old-token")

    class BlockingTester:
        def test(self, _profile: object, _secret: str) -> CredentialTestResult:
            started.set()
            assert release.wait(1.0)
            return CredentialTestResult(
                success=True,
                tested_at=fixed_now(),
                status_text="通过",
                permission_summary="基础",
                expires_at="未知",
            )

    controller = DataSourceSettingsController(store=store, tester=BlockingTester())
    completed: list[CredentialTestResult] = []
    thread = threading.Thread(
        target=lambda: completed.append(
            controller.test_candidate(
                "primary",
                "new-token",
                base_url=str(primary_profile().base_url).rstrip("/"),
                use_system_proxy=False,
            )
        )
    )
    thread.start()
    assert started.wait(1.0)

    second = controller.test_candidate(
        "primary",
        "another-token",
        base_url=str(primary_profile().base_url).rstrip("/"),
        use_system_proxy=False,
    )
    assert not second.success
    assert second.safe_reason == "check_in_progress"
    assert store.get(PRIMARY_CREDENTIAL) == "old-token"

    release.set()
    thread.join(timeout=1.0)
    assert len(completed) == 1 and completed[0].success
    assert store.get(PRIMARY_CREDENTIAL) == "old-token"


def test_failed_primary_replacement_keeps_the_previous_token() -> None:
    store = MemoryCredentialStore()
    store.set(PRIMARY_CREDENTIAL, "old-token")

    class RejectingTester:
        def test(self, _profile: object, _secret: str) -> CredentialTestResult:
            return CredentialTestResult(
                success=False,
                tested_at=fixed_now(),
                status_text="凭据无效",
                permission_summary="未替换",
                expires_at="未知",
                safe_reason="credential_invalid",
            )

    controller = DataSourceSettingsController(store=store, tester=RejectingTester())
    outcome = controller.test_candidate(
        "primary",
        "bad-token",
        base_url=str(primary_profile().base_url).rstrip("/"),
        use_system_proxy=False,
    )

    assert not outcome.success
    assert not controller.commit_candidate("primary", confirmed=True)
    assert store.get(PRIMARY_CREDENTIAL) == "old-token"


def test_capability_coordinator_refuses_a_second_background_run() -> None:
    entered = threading.Event()
    release = threading.Event()

    @dataclass
    class SlowPro:
        calls: list[TransportRequest] = field(default_factory=list)

        def execute(self, request: TransportRequest) -> TransportResult:
            self.calls.append(request)
            if request.api_name == "stock_basic":
                entered.set()
                assert release.wait(1.0)
                return stock_list()
            return result(({"ok": True},))

    pro = SlowPro()
    native = SequenceTransport([realtime(1), realtime(100), realtime(300), realtime(800)])
    coordinator = CapabilityCheckCoordinator(
        cast(ProProxyTransport, pro),
        cast(NativeRealtimeTransport, native),
        request_budget=ApplicationRequestBudget(),
        clock=fixed_now,
    )

    assert coordinator.start_background()
    assert entered.wait(1.0)
    assert not coordinator.start_background()
    release.set()
    deadline = time.monotonic() + 1.0
    while coordinator.in_flight and time.monotonic() < deadline:
        time.sleep(0.005)
    assert not coordinator.in_flight
    coordinator.shutdown()


def test_pro_and_native_transports_share_one_request_start_budget() -> None:
    manual = ManualTime()
    budget = ApplicationRequestBudget(
        clock=manual.monotonic,
        sleeper=manual.sleep,
    )
    pro_session = FakeSession(http_response(200))
    pro = TushareSdkProTransport(
        primary_profile(),
        lambda: "test-token",
        session=cast(requests.Session, pro_session),
        request_budget=budget,
    )

    @dataclass
    class NativeClient:
        version: str = "test"

        def configure(self, _token: str, _verify_url: str) -> None:
            return

        def fetch(
            self,
            codes: tuple[str, ...],
            *,
            source: str,
        ) -> list[dict[str, object]]:
            assert source == "sina"
            return [
                {
                    "TS_CODE": item,
                    "DATE": "2026-07-30",
                    "TIME": "10:00:00",
                    "PRICE": 10,
                    "PRE_CLOSE": 9.9,
                    "OPEN": 9.8,
                    "HIGH": 10.1,
                    "LOW": 9.7,
                    "VOLUME": 1,
                    "AMOUNT": 10,
                }
                for item in codes
            ]

    native = NativeRealtimeTransport(
        NativeRealtimeProfile(),
        lambda: "test-token",
        client=NativeClient(),
        clock=fixed_now,
        monotonic=manual.monotonic,
        sleeper=manual.sleep,
        request_budget=budget,
    )

    pro.execute(TransportRequest(endpoint="/", api_name="trade_cal", allow_empty=True))
    native.execute(
        TransportRequest(
            endpoint="tushare.realtime_quote:sina",
            api_name="realtime_quote",
            params={"ts_code": code(1)},
            realtime=True,
            method="SDK",
        )
    )

    assert manual.sleeps == [1.0]


def test_pro_429_cooldown_does_not_block_native_realtime_lane() -> None:
    manual = ManualTime()
    budget = ApplicationRequestBudget(
        clock=manual.monotonic,
        sleeper=manual.sleep,
    )
    pro = TushareSdkProTransport(
        primary_profile(),
        lambda: "test-token",
        session=cast(requests.Session, FakeSession(http_response(429))),
        request_budget=budget,
    )

    @dataclass
    class LaneNativeClient:
        version: str = "test"
        calls: list[tuple[str, ...]] = field(default_factory=list)

        def configure(self, _token: str, _verify_url: str) -> None:
            return

        def fetch(
            self,
            codes: tuple[str, ...],
            *,
            source: str,
        ) -> list[dict[str, object]]:
            assert source == "sina"
            self.calls.append(codes)
            return [
                {
                    "TS_CODE": item,
                    "DATE": "2026-07-30",
                    "TIME": "10:00:00",
                    "PRICE": 10,
                    "PRE_CLOSE": 9.9,
                    "OPEN": 9.8,
                    "HIGH": 10.1,
                    "LOW": 9.7,
                    "VOLUME": 1,
                    "AMOUNT": 10,
                }
                for item in codes
            ]

    native_client = LaneNativeClient()
    native = NativeRealtimeTransport(
        NativeRealtimeProfile(),
        lambda: "test-token",
        client=native_client,
        clock=fixed_now,
        monotonic=manual.monotonic,
        sleeper=manual.sleep,
        request_budget=budget,
    )

    with pytest.raises(ProviderError):
        pro.execute(TransportRequest(endpoint="/", api_name="stock_basic"))
    native.execute(
        TransportRequest(
            endpoint="tushare.realtime_quote:sina",
            api_name="realtime_quote",
            params={"ts_code": code(1)},
            realtime=True,
            method="SDK",
        )
    )

    assert manual.sleeps == [1.0]
    assert budget.cooldown_remaining(lane="pro") == 59.0
    assert budget.cooldown_remaining(lane="realtime") == 0.0
    assert len(native_client.calls) == 1


def test_native_realtime_preserves_supplier_retry_after() -> None:
    manual = ManualTime()
    budget = ApplicationRequestBudget(
        clock=manual.monotonic,
        sleeper=manual.sleep,
    )

    @dataclass
    class RateLimitedNativeClient:
        version: str = "test"

        def configure(self, _token: str, _verify_url: str) -> None:
            return

        def fetch(
            self,
            _codes: tuple[str, ...],
            *,
            source: str,
        ) -> list[dict[str, object]]:
            assert source == "sina"
            raise ProviderError(
                ProviderFailureReason.RATE_LIMITED,
                retry_after_seconds=17.0,
            )

    transport = NativeRealtimeTransport(
        NativeRealtimeProfile(),
        lambda: "test-token",
        client=RateLimitedNativeClient(),
        clock=fixed_now,
        monotonic=manual.monotonic,
        sleeper=manual.sleep,
        request_budget=budget,
    )

    with pytest.raises(ProviderError) as raised:
        transport.execute(
            TransportRequest(
                endpoint="tushare.realtime_quote:sina",
                api_name="realtime_quote",
                params={"ts_code": code(1)},
                realtime=True,
                method="SDK",
            )
        )

    assert raised.value.reason is ProviderFailureReason.RATE_LIMITED
    assert raised.value.retry_after_seconds == 17.0
    assert budget.cooldown_remaining() == 17.0


def test_capability_start_background_after_shutdown_returns_false() -> None:
    coordinator = CapabilityCheckCoordinator(
        cast(ProProxyTransport, SequenceTransport([])),
        cast(NativeRealtimeTransport, SequenceTransport([])),
        request_budget=ApplicationRequestBudget(),
    )
    coordinator.shutdown()
    assert coordinator.start_background() is False
    assert coordinator.start_realtime_background() is False
    coordinator.shutdown()
