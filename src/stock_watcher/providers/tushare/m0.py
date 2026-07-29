from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_watcher.config import DataSourceSettings
from stock_watcher.security import (
    FAST_CREDENTIAL,
    SUPER_CREDENTIAL,
    KeyringCredentialStore,
)

from .errors import ProviderError
from .fast_transport import FastTransport
from .super_transport import SuperTransport
from .transport_protocol import TransportRequest, TushareTransport


class M0Verdict(StrEnum):
    PASS = "PASS"
    PASS_WITH_LIMITS = "PASS_WITH_LIMITS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class CapabilityObservation:
    capability: str
    provider_profile: str
    http_status: int | None
    elapsed_ms: float | None
    returned_records: int | None
    source_timestamp_present: bool
    status: str
    safe_reason: str | None = None
    field_names: tuple[str, ...] = ()


@dataclass(slots=True)
class M0Report:
    started_at: datetime
    observations: list[CapabilityObservation] = field(default_factory=list)
    raw_payload_persisted: bool = False
    credential_persisted: bool = False

    def add(self, observation: CapabilityObservation) -> None:
        self.observations.append(observation)

    def verdict(self) -> M0Verdict:
        if any(item.status == "FAIL" for item in self.observations):
            return M0Verdict.FAIL
        if any(not item.source_timestamp_present for item in self.observations):
            return M0Verdict.PASS_WITH_LIMITS
        return M0Verdict.PASS

    def sanitized_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "started_at": self.started_at.isoformat(),
            "verdict": self.verdict().value,
            "observations": [asdict(item) for item in self.observations],
            "raw_payload_persisted": self.raw_payload_persisted,
            "credential_persisted": self.credential_persisted,
        }


def _secret(profile: str) -> str | None:
    store = KeyringCredentialStore()
    reference = SUPER_CREDENTIAL if profile == "super" else FAST_CREDENTIAL
    try:
        stored = store.get(reference)
    except Exception:
        stored = None
    if stored:
        return stored
    environment_name = (
        "STOCKWATCHER_TUSHARE_SUPER_API_KEY"
        if profile == "super"
        else "STOCKWATCHER_TUSHARE_FAST_TOKEN"
    )
    return os.environ.get(environment_name)


def _transport(profile: str, secret: str) -> TushareTransport:
    settings = DataSourceSettings()
    if profile == "super":
        return SuperTransport(settings.super_profile, lambda: secret)
    return FastTransport(settings.fast_profile, lambda: secret)


def _capability_requests(profile: str) -> tuple[tuple[str, TransportRequest], ...]:
    if profile == "super":
        return (
            ("health", TransportRequest(endpoint="/health", method="GET", allow_empty=True)),
            ("status", TransportRequest(endpoint="/status", method="GET", allow_empty=True)),
            (
                "catalog",
                TransportRequest(
                    endpoint="/tushare/pro/catalog", method="GET", allow_empty=True
                ),
            ),
            (
                "trade_cal",
                TransportRequest(
                    endpoint="/tushare/pro/trade_cal",
                    api_name="trade_cal",
                    params={"exchange": "SSE"},
                    fields=("exchange", "cal_date", "is_open"),
                ),
            ),
            (
                "stock_basic",
                TransportRequest(
                    endpoint="/tushare/pro/stock_basic",
                    api_name="stock_basic",
                    params={"list_status": "L"},
                    fields=("ts_code", "name", "market", "list_status"),
                ),
            ),
        )
    return (
        (
            "trade_cal",
            TransportRequest(
                endpoint="/",
                api_name="trade_cal",
                params={"exchange": "SSE"},
                fields=("exchange", "cal_date", "is_open"),
            ),
        ),
        (
            "daily",
            TransportRequest(
                endpoint="/",
                api_name="daily",
                params={"limit": 1},
                fields=("ts_code", "trade_date", "close"),
            ),
        ),
    )


def run_capability_m0(profile: str) -> M0Report:
    started = datetime.now(ZoneInfo("Asia/Shanghai"))
    report = M0Report(started)
    secret = _secret(profile)
    if not secret:
        report.add(
            CapabilityObservation(
                capability="credential",
                provider_profile=profile,
                http_status=None,
                elapsed_ms=None,
                returned_records=None,
                source_timestamp_present=False,
                status="FAIL",
                safe_reason="credential_missing",
            )
        )
        return report
    transport = _transport(profile, secret)
    for capability, request in _capability_requests(profile):
        try:
            result = transport.execute(request)
        except ProviderError as exc:
            report.add(
                CapabilityObservation(
                    capability=capability,
                    provider_profile=profile,
                    http_status=exc.http_status,
                    elapsed_ms=None,
                    returned_records=None,
                    source_timestamp_present=False,
                    status="FAIL",
                    safe_reason=exc.reason.value,
                )
            )
            break
        fields = tuple(sorted({field for record in result.records for field in record}))
        report.add(
            CapabilityObservation(
                capability=capability,
                provider_profile=profile,
                http_status=result.http_status,
                elapsed_ms=round(result.elapsed_seconds * 1000, 3),
                returned_records=len(result.records),
                source_timestamp_present=result.provenance.source_ts is not None,
                status="PASS",
                field_names=fields,
            )
        )
    return report


def write_report(report: M0Report, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report.sanitized_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Sanitized Tushare capability M0")
    parser.add_argument("--profile", choices=("super", "fast"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_capability_m0(args.profile)
    write_report(report, args.output)
    print(f"Tushare {args.profile} capability M0: {report.verdict().value}")
    return 1 if report.verdict() is M0Verdict.FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
