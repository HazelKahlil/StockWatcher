from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
import requests
from pydantic import HttpUrl

from stock_watcher.config import HttpProfile
from stock_watcher.providers.tushare import ProviderError, ProviderFailureReason
from stock_watcher.providers.tushare.fast_transport import FastTransport
from stock_watcher.providers.tushare.sdk_pro_transport import TushareSdkProTransport
from stock_watcher.providers.tushare.super_transport import SuperTransport
from stock_watcher.providers.tushare.transport_protocol import TransportRequest


def response(
    status: int, payload: object, headers: dict[str, str] | None = None
) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode("utf-8")
    result.headers.update(headers or {})
    return result


class FakeSession:
    def __init__(self, responses: list[requests.Response | Exception]) -> None:
        self.responses = responses
        self.requests: list[dict[str, object]] = []
        self.trust_env = True

    def request(self, method: str, url: str, **kwargs: object) -> requests.Response:
        self.requests.append({"method": method, "url": url, **kwargs})
        next_result = self.responses.pop(0)
        if isinstance(next_result, Exception):
            raise next_result
        return next_result


def profile(name: str, *, proxy: bool = False) -> HttpProfile:
    url = (
        "https://ai-tool.indevs.in"
        if name == "super"
        else "https://fastapic.stockai888.top"
    )
    return HttpProfile(
        name=name,
        base_url=HttpUrl(url),
        credential_ref=f"StockWatcher/Tushare/{name.title()}",
        use_system_proxy=proxy,
    )


def fixed_clock() -> datetime:
    return datetime(2026, 7, 29, 10, 0, 3, tzinfo=ZoneInfo("Asia/Shanghai"))


def monotonic(values: list[float]) -> Callable[[], float]:
    def next_value() -> float:
        return values.pop(0)

    return next_value


def test_super_transport_uses_header_and_parses_provenance() -> None:
    fake = FakeSession(
        [
            response(
                200,
                {
                    "code": 0,
                    "data": {
                        "fields": ["source_ts", "close"],
                        "items": [["2026-07-29T10:00:00+08:00", 10.5]],
                    },
                },
            )
        ]
    )
    transport = SuperTransport(
        profile("super"),
        lambda: "new-secret",
        session=cast(requests.Session, fake),
        clock=fixed_clock,
        monotonic=monotonic([1.0, 1.2]),
    )
    result = transport.execute(
        TransportRequest(
            endpoint="/tushare/pro/rt_k",
            params={"ts_code": "000001.SZ"},
            method="GET",
            realtime=True,
        )
    )
    assert result.http_status == 200
    assert result.elapsed_seconds == pytest.approx(0.2)
    assert result.provenance.freshness_seconds == 3.0
    assert result.provenance.quality.value == "HEALTHY"
    request = fake.requests[0]
    assert request["headers"] == {"X-API-Key": "new-secret", "Accept": "application/json"}
    assert request["method"] == "GET"
    assert request["params"] == {"ts_code": "000001.SZ"}
    assert "json" not in request
    assert "new-secret" not in repr(result)


def test_realtime_minute_time_field_is_treated_as_supplier_timestamp() -> None:
    fake = FakeSession(
        [
            response(
                200,
                {
                    "code": 0,
                    "data": [
                        {
                            "time": "2026-07-29 10:00:00",
                            "close": 10.5,
                        }
                    ],
                },
            )
        ]
    )
    transport = SuperTransport(
        profile("super"),
        lambda: "secret",
        session=cast(requests.Session, fake),
        clock=fixed_clock,
        monotonic=monotonic([1.0, 1.1]),
    )
    result = transport.execute(
        TransportRequest(endpoint="/tushare/pro/rt_min", method="GET", realtime=True)
    )
    assert result.provenance.source_ts == datetime(
        2026, 7, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    assert result.provenance.freshness_seconds == 3.0


def test_super_get_sends_requested_fields_as_query_parameter() -> None:
    fake = FakeSession([response(200, {"code": 0, "data": [{"close": 10.5}]})])
    transport = SuperTransport(
        profile("super"),
        lambda: "secret",
        session=cast(requests.Session, fake),
    )
    transport.execute(
        TransportRequest(
            endpoint="/tushare/pro/rt_k",
            params={"ts_code": "3*.SZ"},
            fields=("ts_code", "close", "trade_time"),
            method="GET",
            realtime=True,
        )
    )
    assert fake.requests[0]["params"] == {
        "ts_code": "3*.SZ",
        "fields": "ts_code,close,trade_time",
    }


def test_fast_transport_uses_expected_body_and_never_url_query() -> None:
    fake = FakeSession([response(200, {"code": 0, "data": [{"ok": True}]})])
    transport = FastTransport(
        profile("fast"),
        lambda: "fast-secret",
        session=cast(requests.Session, fake),
        clock=fixed_clock,
        monotonic=monotonic([1.0, 1.1]),
    )
    result = transport.execute(
        TransportRequest(endpoint="/", api_name="daily", params={"limit": 1})
    )
    request = fake.requests[0]
    body = cast(dict[str, Any], request["json"])
    assert body["api_name"] == "daily"
    assert body["token"] == "fast-secret"
    assert "fast-secret" not in cast(str, request["url"])
    assert result.provenance.quality.value == "DEGRADED"


def test_documented_sdk_pro_transport_uses_api_path_and_sdk_contract() -> None:
    fake = FakeSession([response(200, {"code": 0, "data": [{"ok": True}]})])
    transport = TushareSdkProTransport(
        profile("fast"),
        lambda: "sdk-secret",
        session=cast(requests.Session, fake),
        clock=fixed_clock,
        monotonic=monotonic([1.0, 1.1]),
    )

    result = transport.execute(
        TransportRequest(endpoint="/", api_name="daily", params={"limit": 1})
    )

    request = fake.requests[0]
    body = cast(dict[str, Any], request["json"])
    assert request["method"] == "POST"
    assert request["url"] == "https://fastapic.stockai888.top/daily"
    assert request["headers"] == {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "Content-Type": "application/json",
    }
    assert body == {
        "api_name": "daily",
        "token": "sdk-secret",
        "params": {
            "limit": 1,
            "ts_type_name": "https://fastapic.stockai888.top",
        },
        "fields": "",
    }
    assert result.provenance.endpoint == "/daily"
    assert "sdk-secret" not in request["url"]
    assert "sdk-secret" not in repr(result)


def test_documented_sdk_pro_transport_preserves_429_cooldown() -> None:
    fake = FakeSession([response(429, {"code": 0, "data": []})])
    transport = TushareSdkProTransport(
        profile("fast"),
        lambda: "sdk-secret",
        session=cast(requests.Session, fake),
    )

    with pytest.raises(ProviderError) as raised:
        transport.execute(TransportRequest(endpoint="/", api_name="trade_cal"))

    assert raised.value.reason is ProviderFailureReason.RATE_LIMITED
    assert raised.value.retry_after_seconds == 60.0
    assert fake.requests[0]["url"] == "https://fastapic.stockai888.top/trade_cal"


@pytest.mark.parametrize(
    "status, reason",
    [
        (401, ProviderFailureReason.CREDENTIAL_INVALID),
        (403, ProviderFailureReason.PERMISSION_DENIED),
        (429, ProviderFailureReason.RATE_LIMITED),
        (503, ProviderFailureReason.FRESHNESS),
        (500, ProviderFailureReason.SERVER_ERROR),
    ],
)
def test_http_errors_map_to_distinct_safe_reasons(
    status: int, reason: ProviderFailureReason
) -> None:
    fake = FakeSession([response(status, {"secret": "do-not-read"})] * 3)
    transport = SuperTransport(
        profile("super"),
        lambda: "secret",
        session=cast(requests.Session, fake),
        sleeper=lambda _seconds: None,
    )
    with pytest.raises(ProviderError) as captured:
        transport.execute(TransportRequest(endpoint="/health"))
    assert captured.value.reason is reason
    assert "do-not-read" not in str(captured.value)


def test_realtime_request_does_not_retry_or_fallback() -> None:
    fake = FakeSession([requests.Timeout("raw credential=hidden")])
    transport = SuperTransport(
        profile("super"),
        lambda: "secret",
        session=cast(requests.Session, fake),
    )
    with pytest.raises(ProviderError) as captured:
        transport.execute(TransportRequest(endpoint="/tushare/pro/rt_k", realtime=True))
    assert captured.value.reason is ProviderFailureReason.TIMEOUT
    assert len(fake.requests) == 1
    assert "hidden" not in str(captured.value)


def test_static_request_retries_twice_with_backoff() -> None:
    fake = FakeSession(
        [
            response(500, {}),
            response(502, {}),
            response(200, {"code": 0, "data": [{"ok": True}]}),
        ]
    )
    sleeps: list[float] = []
    transport = SuperTransport(
        profile("super"),
        lambda: "secret",
        session=cast(requests.Session, fake),
        sleeper=sleeps.append,
    )
    transport.execute(TransportRequest(endpoint="/health"))
    assert len(fake.requests) == 3
    assert sleeps == [0.25, 0.5]


def test_missing_credential_stops_before_network() -> None:
    fake = FakeSession([])
    transport = SuperTransport(
        profile("super"),
        lambda: None,
        session=cast(requests.Session, fake),
    )
    with pytest.raises(ProviderError) as captured:
        transport.execute(TransportRequest(endpoint="/health"))
    assert captured.value.reason is ProviderFailureReason.CREDENTIAL_MISSING
    assert fake.requests == []


def test_proxy_behavior_is_explicitly_configurable() -> None:
    disabled = FakeSession([])
    SuperTransport(
        profile("super", proxy=False),
        lambda: "secret",
        session=cast(requests.Session, disabled),
    )
    enabled = FakeSession([])
    SuperTransport(
        profile("super", proxy=True),
        lambda: "secret",
        session=cast(requests.Session, enabled),
    )
    assert not disabled.trust_env
    assert enabled.trust_env
