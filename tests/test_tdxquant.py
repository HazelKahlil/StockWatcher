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
from stock_watcher.providers.tdxquant import is_continuous_trading_session
from stock_watcher.providers.tdxquant_m0 import M0Report, _timed, write_report
from stock_watcher.providers.tdxquant_preflight import CheckStatus, run_preflight

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
    monkeypatch.setattr(
        "stock_watcher.providers.tdxquant_preflight.socket.create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("refused")),
    )
    report = run_preflight(require_windows=False, attempt_api=False)
    assert report.status is CheckStatus.WARN
    assert not report.windows_live_verified
    assert report.fund_module == "unavailable"
    service = next(check for check in report.checks if check.name == "tq_service")
    assert service.reason is TdxFailureReason.SERVICE_UNREACHABLE


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
