from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_watcher.config import DataSourceSettings
from stock_watcher.providers.tushare.errors import ProviderError
from stock_watcher.providers.tushare.super_transport import SuperTransport
from stock_watcher.providers.tushare.transport_protocol import TransportRequest
from stock_watcher.security import SUPER_CREDENTIAL, KeyringCredentialStore

SHANGHAI = ZoneInfo("Asia/Shanghai")
REALTIME_PATTERN = "3*.SZ,6*.SH,0*.SZ,9*.BJ"


@dataclass(frozen=True, slots=True)
class RoundObservation:
    slot: int
    received_at: str
    status: str
    elapsed_ms: float | None
    records: int | None
    sh_records: int | None
    sz_records: int | None
    bj_records: int | None
    duplicate_codes: int | None
    missing_codes_vs_baseline: int | None
    source_ts: str | None
    source_age_seconds: float | None
    volume_progressed: bool | None
    amount_progressed: bool | None
    volume_rollback: bool | None
    amount_rollback: bool | None
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


def _report(
    *,
    started_at: datetime,
    duration_seconds: int,
    interval_seconds: int,
    observations: list[RoundObservation],
    missed_slots: int,
    completed: bool,
) -> dict[str, object]:
    successful = [item for item in observations if item.status == "PASS"]
    elapsed = [item.elapsed_ms for item in successful if item.elapsed_ms is not None]
    source_age = [
        item.source_age_seconds
        for item in successful
        if item.source_age_seconds is not None
    ]
    errors = len(observations) - len(successful)
    fresh = [age for age in source_age if age <= 10]
    verdict = (
        "PASS"
        if completed
        and not errors
        and len(fresh) == len(successful)
        and successful
        else "PASS_WITH_LIMITS"
        if completed and successful
        else "FAIL"
    )
    return {
        "schema_version": 1,
        "phase": "realtime_market_30m",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(SHANGHAI).isoformat(),
        "requested_duration_seconds": duration_seconds,
        "interval_seconds": interval_seconds,
        "completed": completed,
        "verdict": verdict,
        "raw_payload_persisted": False,
        "credential_persisted": False,
        "candidate_rows_persisted": False,
        "rounds": len(observations),
        "successful_rounds": len(successful),
        "error_rounds": errors,
        "error_rate": round(errors / len(observations), 6) if observations else 1.0,
        "missed_slots": missed_slots,
        "elapsed_ms_p50": _percentile(elapsed, 0.5),
        "elapsed_ms_p95": _percentile(elapsed, 0.95),
        "elapsed_ms_max": round(max(elapsed), 3) if elapsed else None,
        "source_age_seconds_p50": _percentile(source_age, 0.5),
        "source_age_seconds_p95": _percentile(source_age, 0.95),
        "source_age_seconds_max": round(max(source_age), 3) if source_age else None,
        "volume_stagnant_rounds": sum(
            item.volume_progressed is False for item in successful[1:]
        ),
        "amount_stagnant_rounds": sum(
            item.amount_progressed is False for item in successful[1:]
        ),
        "volume_rollback_rounds": sum(
            item.volume_rollback is True for item in successful
        ),
        "amount_rollback_rounds": sum(
            item.amount_rollback is True for item in successful
        ),
        "record_count_p50": (
            statistics.median(
                item.records for item in successful if item.records is not None
            )
            if successful
            else None
        ),
        "observations": [asdict(item) for item in observations],
    }


def run(duration_seconds: int, interval_seconds: int, output: Path) -> int:
    secret = KeyringCredentialStore().get(SUPER_CREDENTIAL)
    if not secret:
        raise SystemExit("Super credential is missing from the system credential store.")
    transport = SuperTransport(
        DataSourceSettings().super_profile,
        lambda: secret,
    )
    started_at = datetime.now(SHANGHAI)
    started_monotonic = time.monotonic()
    observations: list[RoundObservation] = []
    missed_slots = 0
    baseline_codes: set[str] | None = None
    previous_volume: float | None = None
    previous_amount: float | None = None
    slot = 0
    try:
        while True:
            target = started_monotonic + slot * interval_seconds
            remaining = target - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            elif slot and remaining < -interval_seconds:
                skipped = int((-remaining) // interval_seconds)
                missed_slots += skipped
                slot += skipped
            if time.monotonic() - started_monotonic >= duration_seconds:
                break
            received = datetime.now(SHANGHAI)
            strict_failure = False
            try:
                result = transport.execute(
                    TransportRequest(
                        endpoint="/tushare/pro/rt_k",
                        api_name="rt_k",
                        params={"ts_code": REALTIME_PATTERN},
                        fields=(
                            "ts_code",
                            "pre_close",
                            "open",
                            "high",
                            "low",
                            "close",
                            "vol",
                            "amount",
                            "trade_time",
                        ),
                        method="GET",
                        realtime=True,
                    )
                )
            except ProviderError as exc:
                strict_failure = True
                observations.append(
                    RoundObservation(
                        slot=slot,
                        received_at=received.isoformat(),
                        status="FAIL",
                        elapsed_ms=None,
                        records=None,
                        sh_records=None,
                        sz_records=None,
                        bj_records=None,
                        duplicate_codes=None,
                        missing_codes_vs_baseline=None,
                        source_ts=None,
                        source_age_seconds=None,
                        volume_progressed=None,
                        amount_progressed=None,
                        volume_rollback=None,
                        amount_rollback=None,
                        safe_reason=exc.reason.value,
                    )
                )
            else:
                codes = [str(item.get("ts_code", "")) for item in result.records]
                code_set = {code for code in codes if code}
                if baseline_codes is None:
                    baseline_codes = code_set
                volume = sum(float(item.get("vol") or 0) for item in result.records)
                amount = sum(float(item.get("amount") or 0) for item in result.records)
                source_ts = result.provenance.source_ts
                age = (
                    max(
                        0.0,
                        (result.provenance.received_ts - source_ts).total_seconds(),
                    )
                    if source_ts is not None
                    else None
                )
                observations.append(
                    RoundObservation(
                        slot=slot,
                        received_at=result.provenance.received_ts.isoformat(),
                        status="PASS",
                        elapsed_ms=round(result.elapsed_seconds * 1000, 3),
                        records=len(result.records),
                        sh_records=sum(code.endswith(".SH") for code in code_set),
                        sz_records=sum(code.endswith(".SZ") for code in code_set),
                        bj_records=sum(code.endswith(".BJ") for code in code_set),
                        duplicate_codes=len(codes) - len(code_set),
                        missing_codes_vs_baseline=len(baseline_codes - code_set),
                        source_ts=source_ts.isoformat() if source_ts else None,
                        source_age_seconds=round(age, 3) if age is not None else None,
                        volume_progressed=(
                            volume > previous_volume
                            if previous_volume is not None
                            else None
                        ),
                        amount_progressed=(
                            amount > previous_amount
                            if previous_amount is not None
                            else None
                        ),
                        volume_rollback=(
                            volume < previous_volume
                            if previous_volume is not None
                            else None
                        ),
                        amount_rollback=(
                            amount < previous_amount
                            if previous_amount is not None
                            else None
                        ),
                    )
                )
                previous_volume = volume
                previous_amount = amount
            _atomic_write(
                _report(
                    started_at=started_at,
                    duration_seconds=duration_seconds,
                    interval_seconds=interval_seconds,
                    observations=observations,
                    missed_slots=missed_slots,
                    completed=False,
                ),
                output,
            )
            latest = observations[-1]
            print(
                f"slot={slot} status={latest.status} records={latest.records} "
                f"elapsed_ms={latest.elapsed_ms} source_age={latest.source_age_seconds}",
                flush=True,
            )
            if strict_failure:
                break
            slot += 1
    except KeyboardInterrupt:
        pass
    completed = time.monotonic() - started_monotonic >= duration_seconds
    final = _report(
        started_at=started_at,
        duration_seconds=duration_seconds,
        interval_seconds=interval_seconds,
        observations=observations,
        missed_slots=missed_slots,
        completed=completed,
    )
    _atomic_write(final, output)
    print(f"Realtime M0 verdict: {final['verdict']}", flush=True)
    return 0 if completed and observations else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Sanitized Tushare realtime M0")
    parser.add_argument("--duration-seconds", type=int, default=1800)
    parser.add_argument("--interval-seconds", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.duration_seconds < 30:
        parser.error("duration must be at least 30 seconds")
    if args.interval_seconds < 5:
        parser.error("interval must be at least 5 seconds")
    return run(args.duration_seconds, args.interval_seconds, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
