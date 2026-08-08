from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_watcher.config import DataSourceSettings
from stock_watcher.providers.tushare.errors import ProviderError
from stock_watcher.providers.tushare.native_realtime_transport import (
    NativeRealtimeTransport,
)
from stock_watcher.providers.tushare.super_transport import SuperTransport
from stock_watcher.providers.tushare.transport_protocol import TransportRequest
from stock_watcher.security import (
    FAST_CREDENTIAL,
    SUPER_CREDENTIAL,
    KeyringCredentialStore,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
REQUIRED_FIELDS = (
    "ts_code",
    "price",
    "pre_close",
    "vol",
    "amount",
    "source_ts",
    "received_ts",
    "data_quality",
    "volume_unit",
    "amount_unit",
)


@dataclass(frozen=True, slots=True)
class RoundObservation:
    slot: int
    received_at: str
    status: str
    elapsed_ms: float | None
    requested_records: int
    returned_records: int | None
    unique_records: int | None
    missing_records: int | None
    duplicate_records: int | None
    timestamp_coverage: float | None
    today_timestamp_records: int | None
    fresh_records: int | None
    stale_records: int | None
    complete_field_records: int | None
    source_age_seconds_p50: float | None
    source_age_seconds_p95: float | None
    source_age_seconds_max: float | None
    volume_progressed_codes: int | None
    amount_progressed_codes: int | None
    safe_reason: str | None = None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 3)


def _atomic_write(payload: dict[str, object], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(output)


def _in_authoritative_window(started: datetime, duration_seconds: int) -> bool:
    if started.weekday() >= 5:
        return False
    finished = started + timedelta(seconds=duration_seconds)
    morning_start = started.replace(hour=9, minute=30, second=0, microsecond=0)
    morning_end = started.replace(hour=11, minute=30, second=0, microsecond=0)
    afternoon_start = started.replace(hour=13, minute=0, second=0, microsecond=0)
    afternoon_end = started.replace(hour=15, minute=0, second=0, microsecond=0)
    return (morning_start <= started and finished <= morning_end) or (
        afternoon_start <= started and finished <= afternoon_end
    )


def _stock_codes(super_secret: str) -> tuple[str, ...]:
    settings = DataSourceSettings()
    transport = SuperTransport(settings.super_profile, lambda: super_secret)
    result = transport.execute(
        TransportRequest(
            endpoint=f"{settings.super_pro_prefix}/stock_basic",
            api_name="stock_basic",
            params={"list_status": "L"},
            fields=("ts_code",),
            method="GET",
        )
    )
    codes = tuple(
        str(record["ts_code"])
        for record in result.records
        if isinstance(record.get("ts_code"), str)
    )
    if not codes or len(codes) != len(set(codes)):
        raise SystemExit("Sanitized stock universe validation failed.")
    return codes


def _number(record: dict[str, object], field: str) -> float | None:
    value = record.get(field)
    return float(value) if isinstance(value, (int, float)) else None


def _source_date(record: dict[str, object]) -> datetime | None:
    value = record.get("source_ts")
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(SHANGHAI)
    except ValueError:
        return None


def _report(
    *,
    started: datetime,
    duration_seconds: int,
    interval_seconds: int,
    engineering_check: bool,
    completed: bool,
    missed_slots: int,
    observations: list[RoundObservation],
) -> dict[str, object]:
    successful = [item for item in observations if item.status == "PASS"]
    errors = len(observations) - len(successful)
    elapsed = [item.elapsed_ms for item in successful if item.elapsed_ms is not None]
    authoritative = not engineering_check and _in_authoritative_window(
        started, duration_seconds
    )
    verdict = (
        "PASS_WITH_LIMITS"
        if engineering_check and successful
        else "PASS_WITH_LIMITS"
        if completed and successful and not errors
        else "FAIL"
    )
    return {
        "schema_version": 1,
        "phase": "native_realtime_market_m0",
        "authority": (
            "NON_AUTHORITATIVE_ENGINEERING_CHECK"
            if engineering_check
            else "AUTHORITATIVE_TRADING_WINDOW"
            if authoritative
            else "INVALID_WINDOW"
        ),
        "started_at": started.isoformat(),
        "finished_at": datetime.now(SHANGHAI).isoformat(),
        "requested_duration_seconds": duration_seconds,
        "interval_seconds": interval_seconds,
        "completed": completed,
        "verdict": verdict,
        "provider_profile": "native_realtime",
        "provider_route": "tushare.realtime_quote:sina",
        "human_owner_authorized": True,
        "candidate_gate": "CLOSED",
        "candidate_gate_reason": (
            "交易时段连续 M0、真实断线恢复和三周期预热尚未全部完成"
        ),
        "raw_payload_persisted": False,
        "credential_persisted": False,
        "instrument_identifiers_persisted": False,
        "candidate_rows_persisted": False,
        "rounds": len(observations),
        "successful_rounds": len(successful),
        "error_rounds": errors,
        "error_rate": round(errors / len(observations), 6) if observations else 1.0,
        "missed_slots": missed_slots,
        "elapsed_ms_p50": _percentile(elapsed, 0.5),
        "elapsed_ms_p95": _percentile(elapsed, 0.95),
        "elapsed_ms_max": round(max(elapsed), 3) if elapsed else None,
        "network_recovery_exercised": False,
        "warmup_cycles_required": 3,
        "observations": [asdict(item) for item in observations],
    }


def run(
    duration_seconds: int,
    interval_seconds: int,
    output: Path,
    *,
    engineering_check: bool,
) -> int:
    if not engineering_check and duration_seconds < 1800:
        raise SystemExit("Authoritative M0 requires at least 1800 seconds.")
    started = datetime.now(SHANGHAI)
    if not engineering_check and not _in_authoritative_window(
        started, duration_seconds
    ):
        raise SystemExit("Authoritative M0 must fit inside one open trading session.")
    store = KeyringCredentialStore()
    super_secret = store.get(SUPER_CREDENTIAL)
    realtime_secret = store.get(FAST_CREDENTIAL)
    if not super_secret or not realtime_secret:
        raise SystemExit("Required credentials are missing from the system store.")

    codes = _stock_codes(super_secret)
    settings = DataSourceSettings()
    transport = NativeRealtimeTransport(
        settings.native_realtime_profile,
        lambda: realtime_secret,
    )
    request = TransportRequest(
        endpoint="tushare.realtime_quote:sina",
        api_name="realtime_quote",
        params={"ts_code": ",".join(codes)},
        fields=REQUIRED_FIELDS,
        method="SDK",
        realtime=True,
    )
    started_monotonic = time.monotonic()
    observations: list[RoundObservation] = []
    previous: dict[str, tuple[float, float]] = {}
    missed_slots = 0
    slot = 0
    strict_failure = False

    while True:
        target = started_monotonic + slot * interval_seconds
        remaining = target - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        elif slot and remaining < -interval_seconds:
            skipped = int((-remaining) // interval_seconds)
            missed_slots += skipped
            slot += skipped
        if slot and time.monotonic() - started_monotonic >= duration_seconds:
            break
        received = datetime.now(SHANGHAI)
        try:
            result = transport.execute(request)
        except ProviderError as exc:
            strict_failure = True
            observations.append(
                RoundObservation(
                    slot=slot,
                    received_at=received.isoformat(),
                    status="FAIL",
                    elapsed_ms=None,
                    requested_records=len(codes),
                    returned_records=None,
                    unique_records=None,
                    missing_records=None,
                    duplicate_records=None,
                    timestamp_coverage=None,
                    today_timestamp_records=None,
                    fresh_records=None,
                    stale_records=None,
                    complete_field_records=None,
                    source_age_seconds_p50=None,
                    source_age_seconds_p95=None,
                    source_age_seconds_max=None,
                    volume_progressed_codes=None,
                    amount_progressed_codes=None,
                    safe_reason=exc.reason.value,
                )
            )
        else:
            rows = [dict(record) for record in result.records]
            round_received = result.provenance.received_ts
            returned_codes = [
                str(record["ts_code"])
                for record in rows
                if isinstance(record.get("ts_code"), str)
            ]
            unique_codes = set(returned_codes)
            timestamps = [
                parsed
                for record in rows
                if (parsed := _source_date(record)) is not None
            ]
            today = round_received.date()
            today_records = sum(item.date() == today for item in timestamps)
            source_ages = [
                max(0.0, (round_received - item).total_seconds())
                for item in timestamps
                if item.date() == today
            ]
            fresh = [
                record
                for record in rows
                if record.get("data_quality") == "HEALTHY"
            ]
            complete = [
                record
                for record in rows
                if all(record.get(field) is not None for field in REQUIRED_FIELDS)
            ]
            current: dict[str, tuple[float, float]] = {}
            for record in fresh:
                code_value = record.get("ts_code")
                volume = _number(record, "vol")
                amount = _number(record, "amount")
                if isinstance(code_value, str) and volume is not None and amount is not None:
                    current[code_value] = (volume, amount)
            volume_progressed = (
                sum(
                    code_value in previous and value[0] > previous[code_value][0]
                    for code_value, value in current.items()
                )
                if previous
                else None
            )
            amount_progressed = (
                sum(
                    code_value in previous and value[1] > previous[code_value][1]
                    for code_value, value in current.items()
                )
                if previous
                else None
            )
            source_age_p95 = _percentile(source_ages, 0.95)
            strict_failure = (
                len(rows) != len(codes)
                or len(unique_codes) != len(codes)
                or len(timestamps) != len(rows)
                or (
                    not engineering_check
                    and (
                        len(fresh) < int(len(codes) * 0.95)
                        or source_age_p95 is None
                        or (source_age_p95 is not None and source_age_p95 > 10.0)
                        or (bool(previous) and volume_progressed == 0)
                        or (bool(previous) and amount_progressed == 0)
                    )
                )
            )
            observations.append(
                RoundObservation(
                    slot=slot,
                    received_at=round_received.isoformat(),
                    status="FAIL" if strict_failure else "PASS",
                    elapsed_ms=round(result.elapsed_seconds * 1000, 3),
                    requested_records=len(codes),
                    returned_records=len(rows),
                    unique_records=len(unique_codes),
                    missing_records=len(set(codes) - unique_codes),
                    duplicate_records=max(0, len(returned_codes) - len(unique_codes)),
                    timestamp_coverage=round(len(timestamps) / len(rows), 6),
                    today_timestamp_records=today_records,
                    fresh_records=len(fresh),
                    stale_records=len(rows) - len(fresh),
                    complete_field_records=len(complete),
                    source_age_seconds_p50=_percentile(source_ages, 0.5),
                    source_age_seconds_p95=source_age_p95,
                    source_age_seconds_max=(
                        round(max(source_ages), 3) if source_ages else None
                    ),
                    volume_progressed_codes=volume_progressed,
                    amount_progressed_codes=amount_progressed,
                    safe_reason="strict_market_gate_failed" if strict_failure else None,
                )
            )
            previous = current

        elapsed = time.monotonic() - started_monotonic
        completed = elapsed >= duration_seconds and not strict_failure
        _atomic_write(
            _report(
                started=started,
                duration_seconds=duration_seconds,
                interval_seconds=interval_seconds,
                engineering_check=engineering_check,
                completed=completed,
                missed_slots=missed_slots,
                observations=observations,
            ),
            output,
        )
        if strict_failure or engineering_check:
            break
        slot += 1

    completed = (
        time.monotonic() - started_monotonic >= duration_seconds
        and not strict_failure
    )
    _atomic_write(
        _report(
            started=started,
            duration_seconds=duration_seconds,
            interval_seconds=interval_seconds,
            engineering_check=engineering_check,
            completed=completed,
            missed_slots=missed_slots,
            observations=observations,
        ),
        output,
    )
    return 0 if engineering_check and observations else 0 if completed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=int, default=1800)
    parser.add_argument("--interval-seconds", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engineering-check", action="store_true")
    args = parser.parse_args()
    if args.duration_seconds <= 0 or args.interval_seconds <= 0:
        parser.error("duration and interval must be positive")
    return run(
        args.duration_seconds,
        args.interval_seconds,
        args.output,
        engineering_check=args.engineering_check,
    )


if __name__ == "__main__":
    raise SystemExit(main())
