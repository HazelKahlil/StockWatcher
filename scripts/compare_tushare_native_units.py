from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_watcher.config import DataSourceSettings
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
EXPECTED_VOLUME_MULTIPLIER = 100.0
EXPECTED_AMOUNT_MULTIPLIER = 1000.0


def _positive(record: dict[str, object], field: str) -> float | None:
    value = record.get(field)
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number > 0 else None


def _ratio_summary(values: list[float], expected: float) -> dict[str, object]:
    if not values:
        return {
            "samples": 0,
            "median_ratio": None,
            "max_relative_deviation": None,
            "expected_multiplier": expected,
            "matches_expected": False,
        }
    median = statistics.median(values)
    deviations = [abs(value - median) / median for value in values]
    matches = (
        abs(median - expected) / expected <= 0.02
        and max(deviations) <= 0.05
    )
    return {
        "samples": len(values),
        "median_ratio": round(median, 6),
        "max_relative_deviation": round(max(deviations), 6),
        "expected_multiplier": expected,
        "matches_expected": matches,
    }


def _atomic_write(payload: dict[str, object], output: Path) -> None:
    if output.exists():
        raise SystemExit("Unit comparison output already exists; refusing to overwrite.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(output)


def run(*, trade_date: str, sample_size: int, output: Path) -> int:
    if len(trade_date) != 8 or not trade_date.isdigit():
        raise SystemExit("trade date must be YYYYMMDD")
    if not 3 <= sample_size <= 100:
        raise SystemExit("sample size must be between 3 and 100")

    store = KeyringCredentialStore()
    super_secret = store.get(SUPER_CREDENTIAL)
    native_secret = store.get(FAST_CREDENTIAL)
    if not super_secret or not native_secret:
        raise SystemExit("Required credentials are missing from the system store.")

    settings = DataSourceSettings()
    daily = SuperTransport(settings.super_profile, lambda: super_secret).execute(
        TransportRequest(
            endpoint=f"{settings.super_pro_prefix}/daily",
            api_name="daily",
            params={"trade_date": trade_date},
            fields=("ts_code", "vol", "amount"),
            method="GET",
        )
    )
    reference: dict[str, tuple[float, float]] = {}
    for record in daily.records:
        code = record.get("ts_code")
        volume = _positive(record, "vol")
        amount = _positive(record, "amount")
        if isinstance(code, str) and volume is not None and amount is not None:
            reference[code] = (volume, amount)
    codes = tuple(sorted(reference)[:sample_size])
    if len(codes) < sample_size:
        raise SystemExit("Insufficient positive daily records for unit comparison.")

    native = NativeRealtimeTransport(
        settings.native_realtime_profile,
        lambda: native_secret,
    ).execute(
        TransportRequest(
            endpoint="tushare.realtime_quote:sina",
            api_name="realtime_quote",
            params={"ts_code": ",".join(codes)},
            fields=("ts_code", "vol", "amount", "source_ts"),
            method="SDK",
            realtime=True,
        )
    )
    volume_ratios: list[float] = []
    amount_ratios: list[float] = []
    for record in native.records:
        code = record.get("ts_code")
        volume = _positive(record, "vol")
        amount = _positive(record, "amount")
        if (
            not isinstance(code, str)
            or code not in reference
            or volume is None
            or amount is None
        ):
            continue
        daily_volume, daily_amount = reference[code]
        volume_ratios.append(volume / daily_volume)
        amount_ratios.append(amount / daily_amount)

    volume = _ratio_summary(volume_ratios, EXPECTED_VOLUME_MULTIPLIER)
    amount = _ratio_summary(amount_ratios, EXPECTED_AMOUNT_MULTIPLIER)
    passed = (
        len(volume_ratios) >= 3
        and len(amount_ratios) >= 3
        and volume["matches_expected"] is True
        and amount["matches_expected"] is True
    )
    payload: dict[str, object] = {
        "schema_version": 1,
        "phase": "native_realtime_unit_comparison",
        "authority": "POST_CLOSE_CROSS_PROVIDER_COMPARISON",
        "trade_date": trade_date,
        "checked_at": datetime.now(SHANGHAI).isoformat(),
        "verdict": "PASS" if passed else "FAIL",
        "daily_records": len(daily.records),
        "requested_samples": sample_size,
        "matched_samples": len(volume_ratios),
        "volume": {
            **volume,
            "reference_unit": "100_shares",
            "native_unit_if_pass": "shares" if passed else "unverified",
        },
        "amount": {
            **amount,
            "reference_unit": "CNY_thousands",
            "native_unit_if_pass": "CNY" if passed else "unverified",
        },
        "candidate_gate": "CLOSED",
        "raw_payload_persisted": False,
        "instrument_identifiers_persisted": False,
        "raw_values_persisted": False,
        "credential_persisted": False,
        "plaintext_sdk_token_file_present": (
            Path.home() / "tk.csv"
        ).exists(),
    }
    _atomic_write(payload, output)
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    return run(
        trade_date=arguments.trade_date,
        sample_size=arguments.sample_size,
        output=arguments.output,
    )


if __name__ == "__main__":
    raise SystemExit(main())
