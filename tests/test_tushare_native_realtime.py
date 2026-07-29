from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from stock_watcher.config import NativeRealtimeProfile
from stock_watcher.providers.tushare import ProviderError, ProviderFailureReason
from stock_watcher.providers.tushare.native_realtime_transport import (
    NativeRealtimeTransport,
    TushareSdkRealtimeClient,
)
from stock_watcher.providers.tushare.transport_protocol import TransportRequest

SHANGHAI = ZoneInfo("Asia/Shanghai")


def code(index: int) -> str:
    return f"{index:06d}.SZ"


def row(ts_code: str, *, date: str = "2026-07-29", time: str = "10:00:00") -> dict[str, object]:
    return {
        "TS_CODE": ts_code,
        "NAME": "匿名样本",
        "DATE": date,
        "TIME": time,
        "OPEN": 10,
        "PRE_CLOSE": 9.9,
        "PRICE": 10.1,
        "HIGH": 10.2,
        "LOW": 9.8,
        "BID": 10.0,
        "ASK": 10.1,
        "VOLUME": 1000,
        "AMOUNT": 10000,
    }


@dataclass(slots=True)
class FakeClient:
    version: str = "1.4.29-test"
    configured: list[tuple[str, str]] = field(default_factory=list)
    calls: list[tuple[tuple[str, ...], str]] = field(default_factory=list)
    return_none: bool = False
    failure: Exception | None = None
    stale_first: bool = False

    def configure(self, token: str, verify_url: str) -> None:
        self.configured.append((token, verify_url))

    def fetch(
        self,
        codes: tuple[str, ...],
        *,
        source: str,
    ) -> list[dict[str, object]] | None:
        self.calls.append((codes, source))
        if self.failure is not None:
            raise self.failure
        if self.return_none:
            return None
        rows = [row(item) for item in codes]
        if self.stale_first:
            rows[0] = row(codes[0], date="2026-07-28")
        return rows


class FakeFrame:
    def to_dict(self, *, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return [{"TS_CODE": "000001.SH"}]


class FakeSdkModule:
    __version__ = "test"

    def __init__(self, verify_module: object, constants: object) -> None:
        self.verify_module = verify_module
        self.constants = constants

    def set_token(self, _token: str) -> None:
        raise AssertionError("disk-writing SDK set_token must never be called")

    def realtime_quote(self, *, ts_code: str, src: str) -> FakeFrame:
        assert ts_code == "000001.SH"
        assert src == "sina"
        assert getattr(self.verify_module, "get_token")() == "memory-only-secret"
        assert (
            getattr(self.constants, "verify_token_url")
            == "https://realtime.stockai888.top"
        )
        return FakeFrame()


@dataclass(slots=True)
class ManualTime:
    value: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def fixed_clock() -> datetime:
    return datetime(2026, 7, 29, 10, 0, 3, tzinfo=SHANGHAI)


def request(codes: list[str]) -> TransportRequest:
    return TransportRequest(
        endpoint="tushare.realtime_quote:sina",
        api_name="realtime_quote",
        params={"ts_code": ",".join(codes)},
        fields=("ts_code", "price", "vol", "amount", "source_ts"),
        realtime=True,
        method="SDK",
    )


def test_sdk_client_injects_token_only_in_memory_and_restores_globals() -> None:
    verify_module = SimpleNamespace(get_token=lambda: None)
    constants = SimpleNamespace(verify_token_url="https://original.invalid")
    sdk = FakeSdkModule(verify_module, constants)
    modules = {
        "tushare": sdk,
        "tushare.stock.cons": constants,
        "tushare.util.verify_token": verify_module,
    }
    client = TushareSdkRealtimeClient(importer=lambda name: modules[name])

    client.configure(
        "memory-only-secret",
        "https://realtime.stockai888.top",
    )
    assert verify_module.get_token() is None
    assert constants.verify_token_url == "https://original.invalid"

    rows = client.fetch(("000001.SH",), source="sina")

    assert rows == [{"TS_CODE": "000001.SH"}]
    assert verify_module.get_token() is None
    assert constants.verify_token_url == "https://original.invalid"


def test_native_realtime_batches_at_800_and_respects_half_second_floor() -> None:
    client = FakeClient()
    manual = ManualTime()
    transport = NativeRealtimeTransport(
        NativeRealtimeProfile(),
        lambda: "credential",
        client=client,
        clock=fixed_clock,
        monotonic=manual.monotonic,
        sleeper=manual.sleep,
    )
    codes = [code(index) for index in range(1, 1602)]

    result = transport.execute(request(codes))

    assert [len(item[0]) for item in client.calls] == [800, 800, 1]
    assert manual.sleeps == [0.5, 0.5]
    assert len(result.records) == 1601
    assert result.provenance.provider_profile == "native_realtime"
    assert result.provenance.endpoint == "tushare.realtime_quote:sina"
    assert result.provenance.quality.value == "HEALTHY"


def test_native_realtime_normalizes_supplier_timestamp_and_never_exposes_secret() -> None:
    client = FakeClient()
    transport = NativeRealtimeTransport(
        NativeRealtimeProfile(),
        lambda: "do-not-log-this",
        client=client,
        clock=fixed_clock,
    )

    result = transport.execute(request([code(1)]))

    record = result.records[0]
    assert record["ts_code"] == code(1)
    assert record["source_ts"] == "2026-07-29T10:00:00+08:00"
    assert record["received_ts"] == "2026-07-29T10:00:03+08:00"
    assert record["freshness_seconds"] == 3.0
    assert record["data_quality"] == "HEALTHY"
    assert record["volume_unit"] == "shares"
    assert record["amount_unit"] == "CNY"
    assert result.provenance.freshness_seconds == 3.0
    assert client.configured == [
        ("do-not-log-this", "https://realtime.stockai888.top")
    ]
    assert "do-not-log-this" not in repr(result)


def test_native_realtime_marks_old_rows_stale_without_discarding_them() -> None:
    client = FakeClient(stale_first=True)
    transport = NativeRealtimeTransport(
        NativeRealtimeProfile(),
        lambda: "credential",
        client=client,
        clock=fixed_clock,
    )

    result = transport.execute(request([code(1), code(2)]))

    assert result.records[0]["data_quality"] == "STALE"
    assert result.records[1]["data_quality"] == "HEALTHY"
    assert result.provenance.quality.value == "STALE"
    assert result.provenance.degraded


def test_native_realtime_rejects_duplicates_before_supplier_call() -> None:
    client = FakeClient()
    transport = NativeRealtimeTransport(
        NativeRealtimeProfile(),
        lambda: "credential",
        client=client,
    )

    with pytest.raises(ValueError, match="duplicate"):
        transport.execute(request([code(1), code(1)]))

    assert client.configured == []
    assert client.calls == []


def test_native_realtime_none_fails_closed_as_empty_data() -> None:
    client = FakeClient(return_none=True)
    transport = NativeRealtimeTransport(
        NativeRealtimeProfile(),
        lambda: "credential",
        client=client,
    )

    with pytest.raises(ProviderError) as captured:
        transport.execute(request([code(1)]))

    assert captured.value.reason is ProviderFailureReason.EMPTY_DATA


def test_native_realtime_maps_sdk_failure_without_leaking_upstream_message() -> None:
    client = FakeClient(failure=RuntimeError("secret body and identifier"))
    transport = NativeRealtimeTransport(
        NativeRealtimeProfile(),
        lambda: "credential",
        client=client,
    )

    with pytest.raises(ProviderError) as captured:
        transport.execute(request([code(1)]))

    assert captured.value.reason is ProviderFailureReason.BUSINESS_ERROR
    assert "secret body" not in str(captured.value)


def test_native_realtime_profile_forbids_batches_above_verified_limit() -> None:
    with pytest.raises(ValueError):
        NativeRealtimeProfile(batch_size=801)
