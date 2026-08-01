from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from stock_watcher.providers.tushare.m0 import (
    CapabilityObservation,
    M0Report,
    M0Verdict,
    run_capability_m0,
)


def report() -> M0Report:
    return M0Report(datetime(2026, 7, 29, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))


def observation(
    *,
    status: str = "PASS",
    source_timestamp_present: bool = True,
) -> CapabilityObservation:
    return CapabilityObservation(
        capability="anonymous_capability",
        provider_profile="super",
        http_status=200,
        elapsed_ms=12.5,
        returned_records=1,
        source_timestamp_present=source_timestamp_present,
        status=status,
    )


def test_m0_fails_when_any_strict_capability_fails() -> None:
    result = report()
    result.add(observation())
    result.add(observation(status="FAIL"))
    assert result.verdict() is M0Verdict.FAIL


def test_m0_is_limited_when_source_timestamp_is_missing() -> None:
    result = report()
    result.add(observation(source_timestamp_present=False))
    assert result.verdict() is M0Verdict.PASS_WITH_LIMITS


def test_m0_is_limited_when_optional_capability_is_unavailable() -> None:
    result = report()
    result.add(observation())
    unavailable = observation()
    result.add(
        CapabilityObservation(
            capability=unavailable.capability,
            provider_profile=unavailable.provider_profile,
            http_status=404,
            elapsed_ms=unavailable.elapsed_ms,
            returned_records=0,
            source_timestamp_present=True,
            status="UNAVAILABLE",
            safe_reason="endpoint_not_available",
        )
    )
    assert result.verdict() is M0Verdict.PASS_WITH_LIMITS


def test_m0_sanitized_report_never_contains_payload_or_credentials() -> None:
    result = report()
    result.add(observation())
    rendered = result.sanitized_dict()
    assert rendered["verdict"] == "PASS"
    assert rendered["raw_payload_persisted"] is False
    assert rendered["credential_persisted"] is False
    observations = rendered["observations"]
    assert isinstance(observations, list)
    assert all(isinstance(item, dict) and "records" not in item for item in observations)


def test_capability_m0_without_credential_fails_before_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "stock_watcher.providers.tushare.m0._secret", lambda _profile: None
    )
    result = run_capability_m0("super")
    assert result.verdict() is M0Verdict.FAIL
    assert result.observations[0].safe_reason == "credential_missing"
