from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest

from stock_watcher.domain import (
    SHANGHAI,
    DataQuality,
    HealthState,
    SourceTimestampKind,
    TradingDate,
)
from stock_watcher.providers import (
    ProviderReadiness,
    ProviderUnavailable,
    TdxFailureReason,
    TdxHttpTransport,
    TdxPythonTransport,
    TdxQuantConfig,
    TdxQuantProvider,
    TdxTransportError,
    provider_descriptor,
)
from stock_watcher.providers.tdxquant import FAILURE_MESSAGES_ZH, is_continuous_trading_session
from stock_watcher.providers.tdxquant_m0 import M0Report, _timed, write_report
from stock_watcher.providers.tdxquant_preflight import (
    CheckStatus,
    PreflightCheck,
    PreflightReport,
    main,
    run_preflight,
)

FIXTURES = Path(__file__).parent / "fixtures" / "tdxquant"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeTransport:
    name = "fake-official-contract"

    def __init__(self, responses: Mapping[str, object]) -> None:
        self.responses = dict(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, method: str, params: Mapping[str, object]) -> object:
        self.calls.append((method, dict(params)))
        response = self.responses[method]
        if isinstance(response, list):
            value = response.pop(0)
        else:
            value = response
        if isinstance(value, Exception):
            raise value
        return value


def now() -> datetime:
    return datetime(2026, 7, 23, 10, 0, 2, tzinfo=SHANGHAI)


def provider(
    transport: FakeTransport,
    *,
    stale_after_seconds: float = 20.0,
    min_recovery_samples: int = 3,
    user_paused: bool = False,
) -> TdxQuantProvider:
    config = TdxQuantConfig(
        stock_codes=("600000.SH",),
        stale_after_seconds=stale_after_seconds,
        min_recovery_samples=min_recovery_samples,
        user_paused=user_paused,
    )
    return TdxQuantProvider(transport, config, clock=now)


def test_descriptor_declares_preflight_and_official_capabilities() -> None:
    descriptor = provider_descriptor("tdxquant")
    assert descriptor.readiness is ProviderReadiness.PREFLIGHT_REQUIRED
    assert "official-loopback-http" in descriptor.capabilities
    assert "stock-list" in descriptor.capabilities
    with pytest.raises(ProviderUnavailable, match="preflight"):
        tuple(TdxQuantProvider().events())


def test_http_transport_restricts_endpoint_and_uses_official_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"id": 1, "result": {"ErrorId": "0", "Value": ["600000.SH"]}}'

    def fake_urlopen(request: object, timeout: float) -> Response:
        captured["data"] = getattr(request, "data")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("stock_watcher.providers.tdxquant.urlopen", fake_urlopen)
    transport = TdxHttpTransport(timeout_seconds=1.5)
    assert transport.call("get_stock_list", {"market": "5"}) == ["600000.SH"]
    assert isinstance(captured["data"], bytes)
    assert json.loads(captured["data"].decode("utf-8")) == {
        "id": 1,
        "method": "get_stock_list",
        "params": {"market": "5"},
    }
    with pytest.raises(ValueError, match="loopback"):
        TdxHttpTransport("https://example.com/")
    with pytest.raises(ValueError, match="17709"):
        TdxHttpTransport("http://127.0.0.1:8000/")


def test_http_transport_current_official_bridge_requires_explicit_list_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_params: list[dict[str, object]] = []

    class Response:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return self.payload

    def current_official_bridge(request: object, timeout: float) -> Response:
        assert timeout == 5.0
        body = json.loads(getattr(request, "data").decode("utf-8"))
        params = body["params"]
        captured_params.append(params)
        if params == {"market": "5", "list_type": 0} and type(params["list_type"]) is int:
            result = {"ErrorId": 0, "Value": ["600000.SH"]}
        else:
            result = {"ErrorId": 10}
        return Response(json.dumps({"id": body["id"], "result": result}).encode("utf-8"))

    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant.urlopen", current_official_bridge
    )
    transport = TdxHttpTransport()

    with pytest.raises(TdxTransportError) as old_request:
        transport.call("get_stock_list", {"market": "5"})
    assert old_request.value.reason is TdxFailureReason.INVALID_RESPONSE
    assert transport.call(
        "get_stock_list", {"market": "5", "list_type": 0}
    ) == ["600000.SH"]
    assert captured_params == [
        {"market": "5"},
        {"market": "5", "list_type": 0},
    ]


def test_http_transport_classifies_unreachable_and_vendor_login_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise URLError("refused")

    monkeypatch.setattr("stock_watcher.providers.tdxquant.urlopen", unavailable)
    with pytest.raises(TdxTransportError) as caught:
        TdxHttpTransport().call("get_stock_list", {"market": "5"})
    assert caught.value.reason is TdxFailureReason.SERVICE_UNREACHABLE

    class LoginResponse:
        def __enter__(self) -> LoginResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return '{"result":{"ErrorId":"1","ErrorMsg":"请先登录"}}'.encode()

    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant.urlopen",
        lambda *_args, **_kwargs: LoginResponse(),
    )
    with pytest.raises(TdxTransportError) as login:
        TdxHttpTransport().call("get_stock_list", {"market": "5"})
    assert login.value.reason is TdxFailureReason.NOT_LOGGED_IN


def test_python_transport_is_delayed_and_missing_dependency_is_actionable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def missing(_name: str) -> object:
        raise ImportError

    monkeypatch.setattr("stock_watcher.providers.tdxquant.importlib.import_module", missing)
    transport = TdxPythonTransport(tmp_path / "probe.py")
    with pytest.raises(TdxTransportError) as caught:
        transport.call("get_stock_list", {"market": "5"})
    assert caught.value.reason is TdxFailureReason.DEPENDENCY_MISSING


def test_provider_stock_list_fixes_official_list_type_parameter() -> None:
    transport = FakeTransport({"get_stock_list": ("600000.SH",)})

    securities = provider(transport).stock_list()

    assert [security.code for security in securities] == ["600000.SH"]
    assert transport.calls == [
        ("get_stock_list", {"market": "5", "list_type": 0}),
    ]
    assert type(transport.calls[0][1]["list_type"]) is int


def test_price_volume_mapping_preserves_versions_and_marks_missing_source_time() -> None:
    transport = FakeTransport({"get_pricevol": fixture("price_volume.json")})
    snapshots = provider(transport).price_volume(("600000.SH", "000001.SZ"))
    item = snapshots["600000.SH"]
    assert item.price == 10.35
    assert item.previous_close == 10.20
    assert item.volume == 123456
    assert item.provider_version == "tdxquant-official-unverified"
    assert item.config_version == "v0.3"
    assert item.source_ts == item.received_ts
    assert item.source_timestamp_kind is SourceTimestampKind.RECEIVED_FALLBACK
    assert item.quality is DataQuality.DEGRADED


def test_snapshot_mapping_keeps_unverified_fund_fields_out_of_domain() -> None:
    transport = FakeTransport(
        {
            "get_market_snapshot": fixture("market_snapshot.json"),
            "get_more_info": fixture("more_info.json"),
        }
    )
    item = provider(transport).market_snapshot("600000.SH")
    assert item.source_ts == datetime(2026, 7, 23, 10, 0, 1, tzinfo=SHANGHAI)
    assert item.source_timestamp_kind is SourceTimestampKind.PROVIDER
    assert item.quality is DataQuality.GOOD
    assert item.trading_state == "trading"
    assert not hasattr(item, "Zjl")
    assert not hasattr(item, "purple")
    assert transport.calls[-1][1]["field_list"] == []


def test_historical_mapping_is_timezone_aware_and_bounded() -> None:
    transport = FakeTransport({"get_market_data": fixture("history.json")})
    bars = provider(transport).historical_bars("600000.SH", period="1m", count=2)
    assert len(bars) == 2
    assert bars[0].source_ts == datetime(2026, 7, 23, 9, 59, tzinfo=SHANGHAI)
    assert bars[-1].close == 10.35
    with pytest.raises(ValueError, match="24000"):
        provider(transport).historical_bars("600000.SH", count=24_001)


def test_official_sector_fixture_maps_to_normalized_memberships() -> None:
    transport = FakeTransport({"get_relation": tuple(fixture("sectors.json"))})
    memberships = provider(transport).sectors("688318.SH")
    assert [item.sector_name for item in memberships] == ["软件服务", "互联金融", "中证500"]
    assert memberships[0].security.code == "688318.SH"
    assert memberships[0].sector_code == "881355.SH"
    assert memberships[0].sector_type == "行业"
    assert memberships[0].member_count == 234
    assert memberships[0].effective_date == date(2026, 7, 23)
    assert memberships[0].source_ts == memberships[0].received_ts == now()
    assert memberships[0].provider_version == "tdxquant-official-unverified"
    assert memberships[0].config_version == "v0.3"
    assert memberships[0].quality is DataQuality.DEGRADED
    assert memberships[0].source_timestamp_kind is SourceTimestampKind.RECEIVED_FALLBACK
    assert memberships[-1].sector_code == "0"


@pytest.mark.parametrize("missing", ("BlockName", "BlockType", "GPNume"))
def test_sector_mapping_rejects_missing_official_fields(missing: str) -> None:
    payload = fixture("sectors.json")
    payload[0].pop(missing)
    transport = FakeTransport({"get_relation": tuple(payload)})
    with pytest.raises(TdxTransportError) as caught:
        provider(transport).sectors("688318.SH")
    assert caught.value.reason is TdxFailureReason.FIELD_UNAVAILABLE
    assert caught.value.detail == missing


def test_official_trading_calendar_fixture_maps_to_domain_metadata() -> None:
    transport = FakeTransport({"get_trading_dates": tuple(fixture("trading_dates.json"))})
    dates = provider(transport).trading_dates("20251201", "20251231", count=5)
    assert all(isinstance(item, TradingDate) for item in dates)
    assert dates[0].trading_date == date(2025, 12, 11)
    assert dates[0].market == "SH"
    assert dates[0].is_open
    assert dates[0].source_ts == dates[0].received_ts == now()
    assert dates[0].provider_version == "tdxquant-official-unverified"
    assert dates[0].config_version == "v0.3"
    assert dates[0].quality is DataQuality.DEGRADED
    assert dates[0].source_timestamp_kind is SourceTimestampKind.RECEIVED_FALLBACK
    assert transport.calls == [
        (
            "get_trading_dates",
            {
                "market": "SH",
                "start_time": "20251201",
                "end_time": "20251231",
                "count": 5,
            },
        )
    ]


def test_trading_calendar_rejects_malformed_date() -> None:
    transport = FakeTransport({"get_trading_dates": ("2025-12-11",)})
    with pytest.raises(TdxTransportError) as caught:
        provider(transport).trading_dates()
    assert caught.value.reason is TdxFailureReason.FIELD_UNAVAILABLE


def test_missing_source_timestamp_never_becomes_candidate_safe() -> None:
    more = fixture("more_info.json")
    more.pop("HqTime")
    transport = FakeTransport(
        {
            "get_market_snapshot": fixture("market_snapshot.json"),
            "get_more_info": more,
        }
    )
    event = tuple(provider(transport).events())[0]
    assert event.health.state is HealthState.WARMING
    assert not event.is_candidate_safe
    assert "source_ts" in event.health.detail


def test_duplicate_timestamp_is_processed_once() -> None:
    transport = FakeTransport(
        {
            "get_market_snapshot": fixture("market_snapshot.json"),
            "get_more_info": fixture("more_info.json"),
        }
    )
    configured = TdxQuantProvider(
        transport,
        TdxQuantConfig(stock_codes=("600000.SH", "600000.SH")),
        clock=now,
    )
    assert len(tuple(configured.events())) == 1
    assert tuple(configured.events()) == ()


def test_disconnect_requires_fresh_warming_samples_before_health() -> None:
    snapshot = fixture("market_snapshot.json")
    info = fixture("more_info.json")
    info["HqTime"] = "100003"
    next_info = dict(info)
    next_info["HqTime"] = "100004"
    error = TdxTransportError(TdxFailureReason.DATA_INTERRUPTED)
    transport = FakeTransport(
        {
            "get_market_snapshot": [error, snapshot, snapshot],
            "get_more_info": [info, next_info],
        }
    )
    configured = provider(transport, min_recovery_samples=1)
    assert tuple(configured.events())[0].health.state is HealthState.STOPPED
    assert tuple(configured.events())[0].health.state is HealthState.WARMING
    assert tuple(configured.events())[0].health.state is HealthState.HEALTHY


def test_stale_recovery_requires_cutoff_and_all_configured_warming_samples() -> None:
    snapshot = fixture("market_snapshot.json")
    times = ("095900", "100001", "100003", "100004", "100005", "100006")
    infos = []
    for value in times:
        info = fixture("more_info.json")
        info["HqTime"] = value
        infos.append(info)
    transport = FakeTransport(
        {
            "get_market_snapshot": [snapshot] * len(times),
            "get_more_info": infos,
        }
    )
    configured = provider(transport, min_recovery_samples=3)
    events = [tuple(configured.events())[0] for _ in times]
    assert [event.health.state for event in events] == [
        HealthState.STALE,
        HealthState.STALE,
        HealthState.WARMING,
        HealthState.WARMING,
        HealthState.WARMING,
        HealthState.HEALTHY,
    ]
    assert all(not event.is_candidate_safe for event in events[:-1])
    assert events[-1].is_candidate_safe


def test_duplicate_fresh_timestamp_does_not_advance_or_repeat_recovery() -> None:
    snapshot = fixture("market_snapshot.json")
    times = ("095900", "100003", "100003", "100004", "100005")
    infos = []
    for value in times:
        info = fixture("more_info.json")
        info["HqTime"] = value
        infos.append(info)
    transport = FakeTransport(
        {
            "get_market_snapshot": [snapshot] * len(times),
            "get_more_info": infos,
        }
    )
    configured = provider(transport, min_recovery_samples=2)
    assert tuple(configured.events())[0].health.state is HealthState.STALE
    assert tuple(configured.events())[0].health.state is HealthState.WARMING
    assert tuple(configured.events()) == ()
    assert tuple(configured.events())[0].health.state is HealthState.WARMING
    recovered = tuple(configured.events())[0]
    assert recovered.health.state is HealthState.HEALTHY
    assert recovered.is_candidate_safe


def test_stale_and_user_pause_block_candidates() -> None:
    stale_info = fixture("more_info.json")
    stale_info["HqTime"] = "095900"
    transport = FakeTransport(
        {
            "get_market_snapshot": fixture("market_snapshot.json"),
            "get_more_info": stale_info,
        }
    )
    assert tuple(provider(transport).events())[0].health.state is HealthState.STALE

    paused = provider(FakeTransport({}), user_paused=True)
    event = tuple(paused.events())[0]
    assert event.health.state is HealthState.STOPPED
    assert "暂停" in event.health.detail


@pytest.mark.parametrize(
    ("moment", "expected"),
    (
        (datetime(2026, 7, 23, 9, 45, tzinfo=SHANGHAI), True),
        (datetime(2026, 7, 23, 12, 0, tzinfo=SHANGHAI), False),
        (datetime(2026, 7, 25, 10, 0, tzinfo=SHANGHAI), False),
    ),
)
def test_non_trading_session_classification(moment: datetime, expected: bool) -> None:
    assert is_continuous_trading_session(moment) is expected
    assert is_continuous_trading_session(moment, {date(2026, 7, 23)}) is (
        expected and moment.date() == date(2026, 7, 23)
    )


def test_preflight_on_mac_is_explicit_offline_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("stock_watcher.providers.tdxquant_preflight.sys.platform", "darwin")
    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.socket.create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("refused")),
    )
    report = run_preflight(require_windows=False, attempt_api=False)
    assert report.status is CheckStatus.FAIL
    assert not report.windows_live_verified
    assert report.fund_module == "unavailable"
    service = next(check for check in report.checks if check.name == "tq_service")
    assert service.reason is TdxFailureReason.SERVICE_UNREACHABLE


def test_preflight_on_windows_without_tq_service_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("stock_watcher.providers.tdxquant_preflight.sys.platform", "win32")
    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.socket.create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("refused")),
    )
    report = run_preflight(require_windows=True, attempt_api=False)
    assert report.status is CheckStatus.FAIL
    assert not report.windows_live_verified
    assert report.fund_module == "unavailable"
    operating_system = next(
        check for check in report.checks if check.name == "operating_system"
    )
    assert operating_system.status is CheckStatus.PASS
    service = next(check for check in report.checks if check.name == "tq_service")
    assert service.status is CheckStatus.FAIL
    assert service.reason is TdxFailureReason.SERVICE_UNREACHABLE


class ReachableSocket:
    def __enter__(self) -> ReachableSocket:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _prepare_passing_windows_preflight(
    monkeypatch: pytest.MonkeyPatch, terminal_path: Path
) -> None:
    terminal_path.mkdir()
    monkeypatch.setattr("stock_watcher.providers.tdxquant_preflight.sys.platform", "win32")
    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.importlib.util.find_spec",
        lambda _name: object(),
    )
    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.socket.create_connection",
        lambda *_args, **_kwargs: ReachableSocket(),
    )


@pytest.mark.parametrize(
    "api_result",
    (
        {"unexpected": "opaque"},
        {"stock_list": {"ErrorId": "1", "ErrorMsg": "vendor-secret"}},
        {"stock_list": []},
        ["600000.SH", {"code": "000001.SZ"}],
        ["not-a-stock-code"],
    ),
)
def test_preflight_rejects_malformed_nonempty_stock_list_results(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, api_result: object
) -> None:
    terminal_path = tmp_path / "official-terminal"
    _prepare_passing_windows_preflight(monkeypatch, terminal_path)
    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.TdxHttpTransport.call",
        lambda *_args, **_kwargs: api_result,
    )

    report = run_preflight(terminal_path=terminal_path)

    api_session = next(check for check in report.checks if check.name == "api_session")
    assert api_session.status is CheckStatus.FAIL
    assert api_session.reason in {
        TdxFailureReason.INVALID_RESPONSE,
        TdxFailureReason.NOT_LOGGED_IN,
    }
    assert report.status is CheckStatus.FAIL
    assert not report.windows_live_verified
    assert "vendor-secret" not in report.to_json()


def test_preflight_without_api_check_cannot_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    terminal_path = tmp_path / "official-terminal"
    _prepare_passing_windows_preflight(monkeypatch, terminal_path)

    report = run_preflight(terminal_path=terminal_path, attempt_api=False)

    assert all(check.status is CheckStatus.PASS for check in report.checks)
    assert all(check.name != "api_session" for check in report.checks)
    assert report.status is CheckStatus.FAIL
    assert not report.windows_live_verified


def test_valid_stock_list_produces_exactly_one_passing_api_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    terminal_path = tmp_path / "official-terminal"
    _prepare_passing_windows_preflight(monkeypatch, terminal_path)
    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.TdxHttpTransport.call",
        lambda *_args, **_kwargs: ["600000.SH", "000001.SZ", "920000.BJ"],
    )

    report = run_preflight(terminal_path=terminal_path)

    api_checks = tuple(check for check in report.checks if check.name == "api_session")
    assert len(api_checks) == 1
    assert api_checks[0].status is CheckStatus.PASS
    assert report.status is CheckStatus.PASS
    assert report.windows_live_verified


def test_preflight_fixes_official_stock_list_parameters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    terminal_path = tmp_path / "official-terminal"
    _prepare_passing_windows_preflight(monkeypatch, terminal_path)
    captured: list[tuple[str, dict[str, object]]] = []

    def successful_stock_list(
        _transport: object, method: str, params: Mapping[str, object]
    ) -> object:
        captured.append((method, dict(params)))
        return ["600000.SH"]

    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.TdxHttpTransport.call",
        successful_stock_list,
    )

    report = run_preflight(terminal_path=terminal_path)

    assert captured == [
        ("get_stock_list", {"market": "5", "list_type": 0}),
    ]
    assert type(captured[0][1]["list_type"]) is int
    assert report.status is CheckStatus.PASS
    assert report.windows_live_verified


def test_preflight_vendor_error_is_fail_closed_and_never_live(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    terminal_path = tmp_path / "official-terminal"
    _prepare_passing_windows_preflight(monkeypatch, terminal_path)

    class VendorErrorResponse:
        def __enter__(self) -> VendorErrorResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"id":1,"result":{"ErrorId":10}}'

    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant.urlopen",
        lambda *_args, **_kwargs: VendorErrorResponse(),
    )

    report = run_preflight(terminal_path=terminal_path)

    api_session = next(check for check in report.checks if check.name == "api_session")
    assert api_session.status is CheckStatus.FAIL
    assert api_session.reason is TdxFailureReason.INVALID_RESPONSE
    assert report.status is CheckStatus.FAIL
    assert not report.windows_live_verified


def _canonical_preflight_checks(
    *,
    api_status: CheckStatus = CheckStatus.PASS,
) -> tuple[PreflightCheck, ...]:
    return (
        PreflightCheck("operating_system", CheckStatus.PASS, "ok"),
        PreflightCheck("python", CheckStatus.PASS, "ok"),
        PreflightCheck("terminal_install", CheckStatus.PASS, "ok"),
        PreflightCheck("python_client", CheckStatus.PASS, "ok"),
        PreflightCheck("tq_service", CheckStatus.PASS, "ok"),
        PreflightCheck("api_session", api_status, "ok"),
    )


@pytest.mark.parametrize(
    ("status", "checks", "live", "expected_status", "expected_live"),
    (
        (
            CheckStatus.PASS,
            _canonical_preflight_checks(),
            True,
            CheckStatus.PASS,
            True,
        ),
        (
            CheckStatus.FAIL,
            _canonical_preflight_checks(api_status=CheckStatus.FAIL),
            False,
            CheckStatus.FAIL,
            False,
        ),
    ),
)
def test_preflight_report_accepts_only_consistent_canonical_outcomes(
    status: CheckStatus,
    checks: tuple[PreflightCheck, ...],
    live: bool,
    expected_status: CheckStatus,
    expected_live: bool,
) -> None:
    report = PreflightReport(
        status=status,
        platform="Windows",
        python_version="3.12",
        endpoint="http://127.0.0.1:17709/",
        checks=checks,
        windows_live_verified=live,
    )

    assert report.status is expected_status
    assert report.windows_live_verified is expected_live


@pytest.mark.parametrize(
    ("status", "checks", "live"),
    (
        (CheckStatus.PASS, _canonical_preflight_checks()[:-1], True),
        (
            CheckStatus.PASS,
            (PreflightCheck("api_session", CheckStatus.PASS, "ok"),),
            True,
        ),
        (
            CheckStatus.PASS,
            (
                *_canonical_preflight_checks(),
                PreflightCheck("api_session", CheckStatus.PASS, "duplicate"),
            ),
            True,
        ),
        (
            CheckStatus.PASS,
            (
                PreflightCheck("unknown_check", CheckStatus.PASS, "ok"),
                *_canonical_preflight_checks()[1:],
            ),
            True,
        ),
        (
            CheckStatus.PASS,
            (
                *_canonical_preflight_checks()[:2],
                *_canonical_preflight_checks()[3:],
            ),
            True,
        ),
        (
            CheckStatus.PASS,
            _canonical_preflight_checks(api_status=CheckStatus.FAIL),
            False,
        ),
        (CheckStatus.PASS, _canonical_preflight_checks(), False),
        (
            CheckStatus.FAIL,
            _canonical_preflight_checks(api_status=CheckStatus.FAIL),
            True,
        ),
    ),
)
def test_preflight_report_rejects_malformed_or_contradictory_success(
    status: CheckStatus,
    checks: tuple[PreflightCheck, ...],
    live: bool,
) -> None:
    report = PreflightReport(
        status=status,
        platform="Windows",
        python_version="3.12",
        endpoint="http://127.0.0.1:17709/",
        checks=checks,
        windows_live_verified=live,
    )

    assert report.status is CheckStatus.FAIL
    assert report.windows_live_verified is False


def test_preflight_main_returns_nonzero_for_non_pass_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks = list(_canonical_preflight_checks())
    checks[3] = PreflightCheck("python_client", CheckStatus.WARN, "optional client missing")
    report = PreflightReport(
        status=CheckStatus.WARN,
        platform="Windows",
        python_version="3.12.0",
        endpoint="http://127.0.0.1:17709/",
        checks=tuple(checks),
        windows_live_verified=False,
    )
    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.run_preflight",
        lambda **_kwargs: report,
    )
    monkeypatch.setattr(sys, "argv", ["tdxquant_preflight"])

    assert main() == 2


@pytest.mark.parametrize(
    ("api_result", "expected_reason"),
    (
        (
            TdxTransportError(
                TdxFailureReason.NOT_LOGGED_IN,
                "ErrorMsg=账号 demo-user token=vendor-secret C:\\Users\\demo",
            ),
            TdxFailureReason.NOT_LOGGED_IN,
        ),
        ("unexpected raw response password=vendor-secret", TdxFailureReason.INVALID_RESPONSE),
        (RuntimeError("host=DESKTOP-DEMO detail=vendor-secret"), TdxFailureReason.INVALID_RESPONSE),
    ),
)
def test_preflight_api_failures_are_fail_closed_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    api_result: object,
    expected_reason: TdxFailureReason,
) -> None:
    monkeypatch.setattr("stock_watcher.providers.tdxquant_preflight.sys.platform", "win32")
    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.socket.create_connection",
        lambda *_args, **_kwargs: ReachableSocket(),
    )

    def fake_call(*_args: object, **_kwargs: object) -> object:
        if isinstance(api_result, Exception):
            raise api_result
        return api_result

    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.TdxHttpTransport.call", fake_call
    )
    report = run_preflight()

    api_session = next(check for check in report.checks if check.name == "api_session")
    assert report.status is CheckStatus.FAIL
    assert not report.windows_live_verified
    assert api_session.status is CheckStatus.FAIL
    assert api_session.reason is expected_reason
    assert api_session.message == FAILURE_MESSAGES_ZH[expected_reason]
    rendered = report.to_json().lower()
    for forbidden in (
        "errormsg",
        "demo-user",
        "vendor-secret",
        "desktop-demo",
        "c:\\\\users",
        "password",
        "token",
    ):
        assert forbidden not in rendered


@pytest.mark.parametrize("signal", (KeyboardInterrupt(), SystemExit(7)))
def test_preflight_does_not_swallow_process_control_signals(
    monkeypatch: pytest.MonkeyPatch, signal: BaseException
) -> None:
    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.socket.create_connection",
        lambda *_args, **_kwargs: ReachableSocket(),
    )

    def interrupt(*_args: object, **_kwargs: object) -> object:
        raise signal

    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.TdxHttpTransport.call", interrupt
    )
    with pytest.raises(type(signal)):
        run_preflight(require_windows=False)


def test_preflight_main_writes_sanitized_failure_report_before_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    terminal_path = tmp_path / "官方 终端目录"
    output_path = tmp_path / "脱敏 报告目录" / "预检 结果.json"
    terminal_path.mkdir()
    monkeypatch.setattr("stock_watcher.providers.tdxquant_preflight.sys.platform", "win32")
    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.socket.create_connection",
        lambda *_args, **_kwargs: ReachableSocket(),
    )

    def vendor_failure(*_args: object, **_kwargs: object) -> object:
        raise TdxTransportError(
            TdxFailureReason.NOT_LOGGED_IN,
            "ErrorMsg=请先登录 account=demo token=vendor-secret",
        )

    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.TdxHttpTransport.call",
        vendor_failure,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tdxquant_preflight",
            "--terminal-path",
            str(terminal_path),
            "--output",
            str(output_path),
        ],
    )

    assert main() == 2
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    api_session = next(check for check in payload["checks"] if check["name"] == "api_session")
    assert payload["status"] == "FAIL"
    assert api_session == {
        "name": "api_session",
        "status": "FAIL",
        "message": FAILURE_MESSAGES_ZH[TdxFailureReason.NOT_LOGGED_IN],
        "reason": "not_logged_in",
    }
    rendered = output_path.read_text(encoding="utf-8").lower()
    assert "errormsg" not in rendered
    assert "vendor-secret" not in rendered
    assert "account=demo" not in rendered


def test_preflight_main_converts_ordinary_exception_to_fixed_failure_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output_path = tmp_path / "含 空格与中文" / "tdxquant-preflight.json"

    def unexpected_failure(**_kwargs: object) -> object:
        raise RuntimeError(
            "ErrorMsg=raw account=demo-user hostname=DESKTOP-DEMO "
            "C:\\Users\\demo token=vendor-secret"
        )

    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.run_preflight",
        unexpected_failure,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["tdxquant_preflight", "--output", str(output_path)],
    )

    assert main() == 2
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert set(payload) == {
        "status",
        "platform",
        "python_version",
        "endpoint",
        "checks",
        "fund_module",
        "windows_live_verified",
    }
    assert payload["status"] == "FAIL"
    assert payload["endpoint"] == "http://127.0.0.1:17709/"
    assert payload["windows_live_verified"] is False
    assert payload["checks"] == [
        {
            "name": "api_session",
            "status": "FAIL",
            "message": FAILURE_MESSAGES_ZH[TdxFailureReason.INVALID_RESPONSE],
            "reason": "invalid_response",
        }
    ]
    rendered = output_path.read_text(encoding="utf-8").lower()
    for forbidden in (
        "errormsg",
        "demo-user",
        "desktop-demo",
        "c:\\\\users",
        "token",
        "vendor-secret",
    ):
        assert forbidden not in rendered


def test_preflight_report_does_not_emit_terminal_directory_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    terminal_path = tmp_path / "account-demo token-secret 用户名"
    _prepare_passing_windows_preflight(monkeypatch, terminal_path)

    report = run_preflight(terminal_path=terminal_path, attempt_api=False)

    terminal = next(check for check in report.checks if check.name == "terminal_install")
    assert terminal.message == "已找到指定的官方终端目录。"
    assert terminal_path.name not in report.to_json()
    assert not report.windows_live_verified


def test_sanitized_report_contains_no_secret_or_raw_payload(tmp_path: Path) -> None:
    report = M0Report(
        generated_at=now().isoformat(),
        environment="Windows-test-fixture",
        verdict="PASS_WITH_LIMITS",
        provider="official-tdxquant",
        endpoint="http://127.0.0.1:17709/",
        preflight={"status": "PASS"},
        observations=(),
        fund_module="unavailable",
        windows_live_verified=False,
        limitations=("fixture only",),
    )
    json_path, markdown_path = write_report(report, tmp_path)
    combined = json_path.read_text(encoding="utf-8") + markdown_path.read_text(encoding="utf-8")
    assert "PASS_WITH_LIMITS" in combined
    assert "unavailable" in combined
    assert "password" not in combined.lower()
    assert "token" not in combined.lower()


def test_m0_report_records_normalized_sector_and_calendar_fields(tmp_path: Path) -> None:
    transport = FakeTransport(
        {
            "get_relation": tuple(fixture("sectors.json")),
            "get_trading_dates": tuple(fixture("trading_dates.json")),
        }
    )
    configured = provider(transport)
    _, sector_observation = _timed(
        "sectors", lambda: configured.sectors("688318.SH")
    )
    _, calendar_observation = _timed(
        "trading_calendar", lambda: configured.trading_dates(count=5)
    )
    assert "source_ts" in sector_observation.fields
    assert "received_ts" in calendar_observation.fields
    report = M0Report(
        generated_at=now().isoformat(),
        environment="Windows-test-fixture",
        verdict="PASS_WITH_LIMITS",
        provider="official-tdxquant",
        endpoint="http://127.0.0.1:17709/",
        preflight={"status": "PASS"},
        observations=(sector_observation, calendar_observation),
        fund_module="unavailable",
        windows_live_verified=False,
        limitations=("fixture only",),
    )
    json_path, _ = write_report(report, tmp_path)
    rendered = json_path.read_text(encoding="utf-8")
    assert "sector_type" in rendered
    assert "trading_date" in rendered
    assert "provider_version" in rendered


def test_windows_package_contract_is_offline_checkable() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/check_windows_package.py"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "no Windows/TdxQuant claim" in completed.stdout
