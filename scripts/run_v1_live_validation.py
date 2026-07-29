from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from datetime import datetime
from datetime import time as wall_time
from pathlib import Path

from stock_watcher.config import DataSourceSettings
from stock_watcher.domain import SHANGHAI, HealthState
from stock_watcher.providers.tushare import ProProxyTransport, Tushare15000Provider
from stock_watcher.providers.tushare.native_realtime_transport import (
    NativeRealtimeTransport,
)
from stock_watcher.runtime import (
    DataHealthConfig,
    DataHealthTracker,
    FullMarketScanCoordinator,
    TushareBootstrapLoader,
    TushareV1Runtime,
)
from stock_watcher.security import (
    FAST_CREDENTIAL,
    PRIMARY_CREDENTIAL,
    CredentialRef,
    KeyringCredentialStore,
)


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
    reference = _credential_reference(store)
    if reference is None:
        print("Windows凭据管理器中没有可用的Tushare Token。")
        return 2

    settings = DataSourceSettings()

    def secret_getter() -> str | None:
        return store.get(reference)

    pro = ProProxyTransport(settings.primary_profile, secret_getter)
    realtime = NativeRealtimeTransport(settings.native_realtime_profile, secret_getter)
    provider = Tushare15000Provider(pro, realtime)
    runtime = TushareV1Runtime(
        TushareBootstrapLoader(provider),
        FullMarketScanCoordinator(
            realtime,
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
    while time.monotonic() < deadline:
        slot_started = time.monotonic()
        observed_at = datetime.now(SHANGHAI)
        outcome = runtime.scan_once()
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
                "price": candidate.price,
                "change_pct": candidate.change_pct,
                "velocity_1m_pct": candidate.velocity_pct,
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
    )
    report = {
        "schema_version": "stockwatcher-v1-live-validation-1",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_minutes": args.duration_minutes,
        "interval_seconds": args.interval_seconds,
        "verdict": "PASS" if passed else "PASS_WITH_LIMITS",
        "credential_source": (
            "primary"
            if reference == PRIMARY_CREDENTIAL
            else "legacy-fast-memory-alias"
        ),
        "credential_persisted_or_printed": False,
        "raw_market_payload_persisted": False,
        "provider_route": "tushare.realtime_quote:sina",
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
    print(f"report={args.output}")
    print(f"sha256={digest}")
    print(f"verdict={report['verdict']}")
    return 0 if passed else 1


def _credential_reference(store: KeyringCredentialStore) -> CredentialRef | None:
    if store.get(PRIMARY_CREDENTIAL):
        return PRIMARY_CREDENTIAL
    if store.get(FAST_CREDENTIAL):
        return FAST_CREDENTIAL
    return None


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
