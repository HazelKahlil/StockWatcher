from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from stock_watcher.config import DataSourceSettings
from stock_watcher.providers.tushare.capabilities import (
    CAPABILITY_ORDER,
    ProviderCapability,
    ProviderCapabilityState,
    ProviderCapabilityStatus,
)
from stock_watcher.providers.tushare.models import (
    DataQuality,
    ProviderProvenance,
    SourceTimestampKind,
    TransportResult,
)
from stock_watcher.providers.tushare.transport_protocol import TransportRequest
from stock_watcher.security import FAST_CREDENTIAL, PRIMARY_CREDENTIAL, MemoryCredentialStore

SHANGHAI = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str) -> Any:
    path = ROOT / "scripts" / name
    module_name = f"stockwatcher_test_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    assert isinstance(module, ModuleType)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def live_validation() -> Any:
    return _load_script("run_v1_live_validation.py")


@pytest.fixture(scope="module")
def post_close_review() -> Any:
    return _load_script("run_v1_post_close_review.py")


def _result(
    records: tuple[dict[str, str | int | float | bool | None], ...],
) -> TransportResult:
    now = datetime(2026, 7, 30, 10, 0, tzinfo=SHANGHAI)
    return TransportResult(
        records=records,
        http_status=200,
        elapsed_seconds=0.2,
        provenance=ProviderProvenance(
            provider_profile="test",
            endpoint="test",
            provider_version="test",
            schema_version="v1",
            source_ts=now,
            received_ts=now,
            source_timestamp_kind=SourceTimestampKind.SUPPLIER,
            freshness_seconds=0.0,
            quality=DataQuality.HEALTHY,
            degraded=False,
            fields_used=(),
        ),
    )


@dataclass
class _RealtimeDelegate:
    result: TransportResult
    requests: list[TransportRequest] = field(default_factory=list)

    def execute(self, request: TransportRequest) -> TransportResult:
        self.requests.append(request)
        return self.result


def test_live_validation_accepts_only_primary_credential(live_validation: Any) -> None:
    store = MemoryCredentialStore()
    store.set(FAST_CREDENTIAL, "legacy-only")

    assert live_validation._primary_credential_reference(store) is None

    store.set(PRIMARY_CREDENTIAL, "primary-only")
    assert live_validation._primary_credential_reference(store) == PRIMARY_CREDENTIAL


def test_post_close_review_accepts_only_primary_credential(post_close_review: Any) -> None:
    store = MemoryCredentialStore()
    store.set(FAST_CREDENTIAL, "legacy-only")

    assert post_close_review._primary_credential_reference(store) is None

    store.set(PRIMARY_CREDENTIAL, "primary-only")
    assert post_close_review._primary_credential_reference(store) == PRIMARY_CREDENTIAL


def test_live_validation_records_aggregate_market_progress_without_raw_rows(
    live_validation: Any,
) -> None:
    delegate = _RealtimeDelegate(
        _result(
            (
                {
                    "ts_code": "000001.SZ",
                    "price": 10.0,
                    "vol": 100.0,
                    "amount": 1000.0,
                },
                {
                    "ts_code": "000002.SZ",
                    "price": 20.0,
                    "vol": 200.0,
                    "amount": 4000.0,
                },
            )
        )
    )
    telemetry = live_validation.MarketTelemetryTransport(delegate)

    telemetry.execute(
        TransportRequest(
            endpoint="tushare.realtime_quote:sina",
            api_name="realtime_quote",
            params={"ts_code": "000001.SZ,000002.SZ"},
            realtime=True,
            method="SDK",
        )
    )

    assert telemetry.latest is not None
    record = telemetry.latest.as_record(None)
    assert record["record_count"] == 2
    assert record["priced_count"] == 2
    assert record["price_sum"] == 30.0
    assert record["volume_shares"] == 300.0
    assert record["amount_cny"] == 5000.0
    assert "000001.SZ" not in str(record)
    assert "000002.SZ" not in str(record)


def test_live_validation_report_lists_each_progressive_capability(
    live_validation: Any,
) -> None:
    statuses = {
        capability: ProviderCapabilityStatus(
            capability=capability,
            state=ProviderCapabilityState.AVAILABLE,
            record_count=1,
        )
        for capability in CAPABILITY_ORDER
    }

    records = live_validation._capability_records(statuses)

    assert [record["capability"] for record in records] == [
        capability.value for capability in CAPABILITY_ORDER
    ]
    assert live_validation._all_capabilities_available(statuses)
    assert live_validation._realtime_capabilities_available(statuses)
    statuses[ProviderCapability.REALTIME_800] = ProviderCapabilityStatus(
        capability=ProviderCapability.REALTIME_800,
        state=ProviderCapabilityState.RATE_LIMITED,
    )
    assert not live_validation._all_capabilities_available(statuses)
    assert not live_validation._realtime_capabilities_available(statuses)


def test_live_validation_uses_seven_batches_for_5500_cached_codes(
    live_validation: Any,
) -> None:
    assert live_validation._batch_plan(5500, 800) == [
        800,
        800,
        800,
        800,
        800,
        800,
        700,
    ]


def test_validation_scripts_inject_one_budget_per_product_provider(
    live_validation: Any,
    post_close_review: Any,
) -> None:
    settings = DataSourceSettings()

    def secret_getter() -> str:
        return "test-only-secret"

    pro, realtime, budget = live_validation._build_product_transports(
        settings,
        secret_getter,
    )
    provider, review_budget = post_close_review._build_product_provider(
        settings,
        secret_getter,
    )

    assert pro._request_budget is budget
    assert realtime._request_budget is budget
    assert provider.pro._request_budget is review_budget
    assert provider.realtime._request_budget is review_budget
    assert budget.min_interval_seconds == 1.0
    assert review_budget.min_interval_seconds == 1.0
