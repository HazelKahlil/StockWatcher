from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import time as wall_time
from pathlib import Path
from typing import Protocol

from stock_watcher.config import DataSourceSettings
from stock_watcher.domain import SHANGHAI, HealthState
from stock_watcher.providers.tushare import (
    CAPABILITY_ORDER,
    ApplicationRequestBudget,
    CapabilityCheckCoordinator,
    ProviderCapability,
    ProviderCapabilityState,
    ProviderCapabilityStatus,
    Tushare15000Provider,
    TushareSdkProTransport,
)
from stock_watcher.providers.tushare.models import TransportResult
from stock_watcher.providers.tushare.native_realtime_transport import (
    NativeRealtimeTransport,
)
from stock_watcher.providers.tushare.transport_protocol import TransportRequest
from stock_watcher.runtime import (
    DataHealthConfig,
    DataHealthTracker,
    FullMarketScanCoordinator,
    TushareBootstrapLoader,
    TushareV1Runtime,
)
from stock_watcher.security import (
    PRIMARY_CREDENTIAL,
    CredentialRef,
    CredentialStore,
    KeyringCredentialStore,
)


class RealtimeExecutor(Protocol):
    def execute(self, request: TransportRequest) -> TransportResult: ...


@dataclass(frozen=True, slots=True)
class MarketProgress:
    """Aggregate full-market quote movement without retaining raw payload rows."""

    record_count: int
    priced_count: int
    price_sum: float
    volume_shares: float
    amount_cny: float

    def as_record(self, previous: MarketProgress | None) -> dict[str, float | int | None]:
        return {
            "record_count": self.record_count,
            "priced_count": self.priced_count,
            "price_sum": self.price_sum,
            "price_sum_delta": _delta(self.price_sum, previous.price_sum)
            if previous is not None
            else None,
            "volume_shares": self.volume_shares,
            "volume_shares_delta": _delta(self.volume_shares, previous.volume_shares)
            if previous is not None
            else None,
            "amount_cny": self.amount_cny,
            "amount_cny_delta": _delta(self.amount_cny, previous.amount_cny)
            if previous is not None
            else None,
        }


class MarketTelemetryTransport:
    """Expose only aggregate market movement for the live-validation report."""

    def __init__(self, delegate: RealtimeExecutor) -> None:
        self._delegate = delegate
        self.latest: MarketProgress | None = None

    def clear(self) -> None:
        self.latest = None

    def execute(self, request: TransportRequest) -> TransportResult:
        result = self._delegate.execute(request)
        self.latest = _market_progress(result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the StockWatcher V1 real-market candidate validation"
    )
    parser.add_argument("--duration-minutes", type=float, default=30.0)
    parser.add_argument("--interval-seconds", type=float, default=10.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.duration_minutes <= 0 or not 5 <= args.interval_seconds <= 60:
        parser.error("duration must be positive and interval must be between 5 and 60 seconds")
    now = datetime.now(SHANGHAI)
    current = now.timetz().replace(tzinfo=None)
    if not (
        wall_time(9, 25) <= current <= wall_time(11, 30)
        or wall_time(12, 55) <= current <= wall_time(15, 0)
    ):
        print("必须在A股交易时段运行；未读取或输出Token。")
        return 2

    store = KeyringCredentialStore()
    reference = _primary_credential_reference(store)
    if reference is None:
        print(f"{_storage_label(store)}中没有已保存的统一 Tushare Token。")
        return 2

    settings = DataSourceSettings()

    def secret_getter() -> str | None:
        return store.get(reference)

    pro, realtime, request_budget = _build_product_transports(settings, secret_getter)
    provider = Tushare15000Provider(pro, realtime)
    capability_checks = CapabilityCheckCoordinator(
        pro,
        realtime,
        request_budget=request_budget,
    )
    # This is deliberately serial and reports each independent capability.  A
    # 429 stops at the affected capability, leaves the saved Token untouched,
    # and is recorded below rather than being misreported as an invalid Token.
    capability_checks.run_until_blocked()
    telemetry = MarketTelemetryTransport(realtime)
    runtime = TushareV1Runtime(
        TushareBootstrapLoader(provider),
        FullMarketScanCoordinator(
            telemetry,
            minimum_coverage_ratio=0.99,
            max_source_span_seconds=settings.full_scan_max_seconds,
        ),
        health=DataHealthTracker(
            DataHealthConfig(
                fresh_seconds=settings.source_fresh_seconds,
                stop_seconds=settings.source_stop_seconds,
                recovery_cycles=settings.realtime_warmup_cycles,
            )
        ),
    )
    started = datetime.now(SHANGHAI)
    start_monotonic = time.monotonic()
    deadline = start_monotonic + args.duration_minutes * 60.0
    rounds: list[dict[str, object]] = []
    slot = 0
    previous_progress: MarketProgress | None = None
    while time.monotonic() < deadline:
        slot_started = time.monotonic()
        observed_at = datetime.now(SHANGHAI)
        telemetry.clear()
        outcome = runtime.scan_once()
        progress = telemetry.latest
        stable_codes = (
            tuple(candidate.code for candidate in outcome.batch.candidates)
            if outcome.batch
            else ()
        )
        raw_codes = (
            tuple(candidate.code for candidate in outcome.raw_batch.candidates)
            if outcome.raw_batch
            else ()
        )
        candidates = [
            {
                "rank": rank,
                "code": candidate.code,
                "name": candidate.name,
                "level": (
                    "近｜补位观察" if candidate.is_supplement else candidate.level
                ),
                "formal": candidate.is_formal,
                "sector": candidate.sector,
                "sector_code": candidate.sector_code,
                "sector_type": candidate.sector_type,
                "price": candidate.price,
                "change_pct": candidate.change_pct,
                "velocity_1m_pct": candidate.velocity_pct,
                "core_score": candidate.core_score,
                "total_score": candidate.total_score,
                "fund_label": candidate.fund_label,
                "reasons": list(candidate.reasons[:5]),
            }
            for rank, candidate in enumerate(
                outcome.batch.candidates if outcome.batch else (),
                start=1,
            )
        ]
        rounds.append(
            {
                "slot": slot,
                "observed_at": observed_at.isoformat(),
                "health": outcome.health.value,
                "detail": outcome.detail,
                "elapsed_seconds": outcome.elapsed_seconds,
                "coverage_ratio": outcome.coverage_ratio,
                "source_age_seconds": outcome.source_age_seconds,
                "source_span_seconds": outcome.source_span_seconds,
                "failure_reason": outcome.failure_reason,
                "market_progress": (
                    progress.as_record(previous_progress)
                    if progress is not None
                    else None
                ),
                "stable_replacement": bool(
                    stable_codes and raw_codes and stable_codes != raw_codes
                ),
                "strong_event": (
                    {
                        "triggering_codes": list(outcome.strong_event.triggering_codes),
                        "strength": outcome.strong_event.strength,
                        "funds_unconfirmed": outcome.strong_event.funds_unconfirmed,
                    }
                    if outcome.strong_event
                    else None
                ),
                "top3": candidates,
            }
        )
        if progress is not None:
            previous_progress = progress
        # A first pass that hit 429 leaves its cursor on the failed capability.
        # This synchronous retry becomes a no-op until its Retry-After/default
        # cooldown has elapsed, then resumes without overlapping the scan.
        capability_checks.run_until_blocked()
        slot += 1
        next_slot = start_monotonic + slot * args.interval_seconds
        remaining = next_slot - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        if time.monotonic() - slot_started > 60:
            # The round is still recorded; sequential execution guarantees no overlap.
            continue

    finished = datetime.now(SHANGHAI)
    elapsed = [
        value
        for row in rounds
        if (value := _number(row, "elapsed_seconds")) is not None
    ]
    complete_rounds = [
        row
        for row in rounds
        if (coverage := _number(row, "coverage_ratio")) is not None
        and coverage >= 0.99
    ]
    candidate_rounds = [
        row
        for row in rounds
        if row.get("health") == HealthState.HEALTHY.value
        and len(row.get("top3", [])) == 3  # type: ignore[arg-type]
    ]
    success_rate = len(complete_rounds) / len(rounds) if rounds else 0.0
    maximum_source_age = max(
        (
            source_age
            for row in complete_rounds
            if (source_age := _number(row, "source_age_seconds")) is not None
        ),
        default=float("inf"),
    )
    minimum_coverage_ratio = min(
        (
            coverage
            for row in complete_rounds
            if (coverage := _number(row, "coverage_ratio")) is not None
        ),
        default=None,
    )
    capability_statuses = capability_checks.statuses()
    all_capabilities_available = _all_capabilities_available(capability_statuses)
    metrics = {
        "rounds": len(rounds),
        "complete_rounds": len(complete_rounds),
        "candidate_rounds": len(candidate_rounds),
        "complete_round_success_rate": success_rate,
        "elapsed_seconds_p50": statistics.median(elapsed) if elapsed else None,
        "elapsed_seconds_p95": _percentile(elapsed, 0.95),
        "elapsed_seconds_max": max(elapsed) if elapsed else None,
        "minimum_coverage_ratio": minimum_coverage_ratio,
        "maximum_source_age_seconds": maximum_source_age,
        "duplicate_rejections": sum(
            row.get("failure_reason") == "duplicate" for row in rounds
        ),
        "overlapping_scans": sum(
            row.get("failure_reason") == "overlap" for row in rounds
        ),
        "rate_limited_rounds": sum(
            row.get("failure_reason") == "rate_limited" for row in rounds
        ),
        "failed_rounds": sum(row.get("failure_reason") is not None for row in rounds),
        "rate_limited_capabilities": sum(
            status.state is ProviderCapabilityState.RATE_LIMITED
            for status in capability_statuses.values()
        ),
    }
    fund_capability = (
        runtime.universe.fund_capability
        if runtime.universe is not None
        else None
    )
    passed = (
        success_rate >= 0.95
        and bool(elapsed)
        and _percentile(elapsed, 0.95) <= 30
        and max(elapsed) <= 60
        and minimum_coverage_ratio is not None
        and minimum_coverage_ratio >= 0.99
        and maximum_source_age <= 60
        and metrics["duplicate_rejections"] == 0
        and metrics["overlapping_scans"] == 0
        and bool(candidate_rounds)
        and all_capabilities_available
    )
    report = {
        "schema_version": "stockwatcher-v1-live-validation-2",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_minutes": args.duration_minutes,
        "interval_seconds": args.interval_seconds,
        "verdict": "PASS" if passed else "INCOMPLETE",
        "credential_source": "platform_secure_storage_primary",
        "credential_storage": _storage_label(store),
        "credential_persisted_or_printed": False,
        "raw_market_payload_persisted": False,
        "provider_routes": {
            "ordinary_history_sector": "https://fastapic.stockai888.top",
            "realtime": "tushare.realtime_quote:sina",
        },
        "request_budget": {
            "shared_across_pro_and_realtime": True,
            "request_start_interval_seconds": request_budget.min_interval_seconds,
            "default_429_cooldown_seconds": (
                ApplicationRequestBudget.default_rate_limit_cooldown_seconds
            ),
        },
        "capability_checks": _capability_records(capability_statuses),
        "all_capabilities_available": all_capabilities_available,
        "fund_capability": (
            {
                "capability": fund_capability.capability.value,
                "reason": fund_capability.reason,
                "fields": list(fund_capability.fields),
            }
            if fund_capability is not None
            else {
                "capability": "unavailable",
                "reason": "启动数据未完成",
                "fields": [],
            }
        ),
        "metrics": metrics,
        "rounds": rounds,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    args.output.write_text(rendered + "\n", encoding="utf-8", newline="\n")
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    capability_checks.shutdown()
    print(f"report={args.output}")
    print(f"sha256={digest}")
    print(f"verdict={report['verdict']}")
    return 0 if passed else 1


def _build_product_transports(
    settings: DataSourceSettings,
    secret_getter: Callable[[], str | None],
) -> tuple[TushareSdkProTransport, NativeRealtimeTransport, ApplicationRequestBudget]:
    budget = ApplicationRequestBudget(settings.request_budget_interval_seconds)
    return (
        TushareSdkProTransport(
            settings.primary_profile,
            secret_getter,
            request_budget=budget,
        ),
        NativeRealtimeTransport(
            settings.native_realtime_profile,
            secret_getter,
            request_budget=budget,
        ),
        budget,
    )


def _primary_credential_reference(store: CredentialStore) -> CredentialRef | None:
    return PRIMARY_CREDENTIAL if store.get(PRIMARY_CREDENTIAL) else None


def _storage_label(store: object) -> str:
    label = getattr(store, "storage_label", None)
    return label if isinstance(label, str) and label else "系统安全存储"


def _capability_records(
    statuses: dict[ProviderCapability, ProviderCapabilityStatus],
) -> list[dict[str, object]]:
    return [
        {
            "capability": capability.value,
            "state": status.state.value,
            "record_count": status.record_count,
            "elapsed_seconds": status.elapsed_seconds,
            "safe_reason": status.safe_reason,
            "checked_at": status.checked_at.isoformat()
            if status.checked_at is not None
            else None,
            "last_success_at": status.last_success_at.isoformat()
            if status.last_success_at is not None
            else None,
            "next_retry_at": status.next_retry_at.isoformat()
            if status.next_retry_at is not None
            else None,
        }
        for capability in CAPABILITY_ORDER
        if (status := statuses.get(capability)) is not None
    ]


def _all_capabilities_available(
    statuses: dict[ProviderCapability, ProviderCapabilityStatus],
) -> bool:
    return all(
        statuses.get(capability, ProviderCapabilityStatus(capability)).state
        is ProviderCapabilityState.AVAILABLE
        for capability in CAPABILITY_ORDER
    )


def _market_progress(result: TransportResult) -> MarketProgress:
    prices = [_as_float(row.get("price")) for row in result.records]
    return MarketProgress(
        record_count=len(result.records),
        priced_count=sum(value is not None and value > 0 for value in prices),
        price_sum=round(sum(value or 0.0 for value in prices), 6),
        volume_shares=round(
            sum(_as_float(row.get("vol")) or 0.0 for row in result.records),
            6,
        ),
        amount_cny=round(
            sum(_as_float(row.get("amount")) or 0.0 for row in result.records),
            6,
        ),
    )


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _delta(current: float, previous: float) -> float:
    return round(current - previous, 6)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return float("inf")
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.9999)))
    return ordered[index]


def _number(row: dict[str, object], key: str) -> float | None:
    value = row.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
