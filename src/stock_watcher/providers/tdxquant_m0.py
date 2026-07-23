from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from stock_watcher.domain import SHANGHAI

from .tdxquant import TdxHttpTransport, TdxQuantConfig, TdxQuantProvider, TdxTransportError
from .tdxquant_preflight import CheckStatus, PreflightReport, run_preflight


@dataclass(frozen=True, slots=True)
class ProbeObservation:
    capability: str
    status: str
    p50_ms: float
    p95_ms: float
    row_count: int
    fields: tuple[str, ...]
    limitation: str = ""


@dataclass(frozen=True, slots=True)
class M0Report:
    generated_at: str
    environment: str
    verdict: str
    provider: str
    endpoint: str
    preflight: dict[str, Any]
    observations: tuple[ProbeObservation, ...]
    fund_module: str
    windows_live_verified: bool
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _timed(
    capability: str, operation: Any, *, attempts: int = 1
) -> tuple[object, ProbeObservation]:
    if attempts < 1:
        raise ValueError("attempts must be positive")
    durations: list[float] = []
    value: object = None
    for _ in range(attempts):
        started = time.perf_counter()
        value = operation()
        durations.append((time.perf_counter() - started) * 1000)
    if isinstance(value, dict):
        row_count = len(value)
        first: object = next(iter(value.values()), {})
        fields = tuple(sorted(str(field) for field in first)) if isinstance(first, dict) else ()
    elif isinstance(value, (list, tuple)):
        row_count = len(value)
        fields = ()
    else:
        row_count = int(value is not None)
        fields = ()
    ordered = sorted(durations)
    p50 = statistics.median(ordered)
    p95_index = min(len(ordered) - 1, max(0, int((len(ordered) * 0.95) - 0.000001)))
    return value, ProbeObservation(capability, "PASS", p50, ordered[p95_index], row_count, fields)


def run_m0_probe(
    *,
    endpoint: str = "http://127.0.0.1:17709/",
    sample_codes: tuple[str, ...] = ("600000.SH", "000001.SZ", "920000.BJ"),
    preflight: PreflightReport | None = None,
) -> M0Report:
    checked = preflight or run_preflight(endpoint=endpoint)
    observations: list[ProbeObservation] = []
    limitations = [
        "本报告不证明供应商书面授权、完整交易时段稳定性、Windows UI 或安装体验。",
        "官方公开快照未承诺精确 source_ts；缺失时 Provider 使用 received_ts 并保持 WARMING。",
        "紫黄线、Level-2、Zjl/Zjl_HB 与资金公式映射未完成现场 M0，资金模块保持 unavailable。",
    ]
    if checked.status is CheckStatus.FAIL:
        return M0Report(
            generated_at=datetime.now(SHANGHAI).isoformat(),
            environment=checked.platform,
            verdict="FAIL",
            provider="official-tdxquant",
            endpoint=endpoint,
            preflight=checked.to_dict(),
            observations=(),
            fund_module="unavailable",
            windows_live_verified=False,
            limitations=tuple(limitations),
        )
    transport = TdxHttpTransport(endpoint)
    discovery_provider = TdxQuantProvider(
        transport,
        TdxQuantConfig(config_version="v0.3-m0"),
    )
    try:
        securities, stock_list_observation = _timed(
            "stock_list", lambda: discovery_provider.stock_list("5")
        )
    except (TdxTransportError, ValueError) as error:
        observations.append(ProbeObservation("stock_list", "FAIL", 0.0, 0.0, 0, (), str(error)))
        selected_codes = sample_codes
    else:
        observations.append(stock_list_observation)
        available = tuple(
            getattr(security, "code", "") for security in cast(tuple[object, ...], securities)
        )
        selected_codes = tuple(
            next((code for code in available if code.endswith(f".{market}")), fallback)
            for market, fallback in zip(("SH", "SZ", "BJ"), sample_codes, strict=True)
        )
    provider = TdxQuantProvider(
        transport,
        TdxQuantConfig(stock_codes=selected_codes, config_version="v0.3-m0"),
    )
    operations = (
        ("price_volume", lambda: provider.price_volume(selected_codes), 3),
        ("market_snapshot", lambda: provider.market_snapshot(selected_codes[0]), 3),
        (
            "historical_bars",
            lambda: provider.historical_bars(selected_codes[0], period="1m", count=30),
            1,
        ),
        ("sectors", lambda: provider.sectors(selected_codes[0]), 1),
        ("trading_calendar", lambda: provider.trading_dates(), 1),
    )
    for capability, operation, attempts in operations:
        try:
            _, observation = _timed(capability, operation, attempts=attempts)
        except (TdxTransportError, ValueError) as error:
            observations.append(ProbeObservation(capability, "FAIL", 0.0, 0.0, 0, (), str(error)))
        else:
            observations.append(observation)
    verdict = "PASS_WITH_LIMITS" if all(item.status == "PASS" for item in observations) else "FAIL"
    return M0Report(
        generated_at=datetime.now(SHANGHAI).isoformat(),
        environment=checked.platform,
        verdict=verdict,
        provider="official-tdxquant",
        endpoint=endpoint,
        preflight=checked.to_dict(),
        observations=tuple(observations),
        fund_module="unavailable",
        windows_live_verified=checked.windows_live_verified,
        limitations=tuple(limitations),
    )


def write_report(report: M0Report, output_directory: Path) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "tdxquant-m0-report.json"
    markdown_path = output_directory / "tdxquant-m0-report.md"
    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rows = "\n".join(
        f"| {item.capability} | {item.status} | {item.p50_ms:.1f} | "
        f"{item.p95_ms:.1f} | {item.row_count} | {item.limitation or '—'} |"
        for item in report.observations
    )
    markdown_path.write_text(
        "\n".join(
            (
                "# StockWatcher TdxQuant M0 报告",
                "",
                f"- 结论：`{report.verdict}`",
                f"- 生成时间：{report.generated_at}",
                f"- 环境：{report.environment}",
                f"- Provider：`{report.provider}`",
                f"- 资金模块：`{report.fund_module}`",
                "",
                "| 能力 | 状态 | p50(ms) | p95(ms) | 行数 | 限制 |",
                "| --- | --- | ---: | ---: | ---: | --- |",
                rows or "| 未执行 | FAIL | 0 | 0 | 0 | 预检未通过 |",
                "",
                "## 限制与后续现场项",
                "",
                *(f"- {item}" for item in report.limitations),
                "",
                "本报告不含敏感凭证、原始行情明细或本机绝对路径。",
            )
        ),
        encoding="utf-8",
    )
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the sanitized official TdxQuant M0 probe")
    parser.add_argument("--endpoint", default="http://127.0.0.1:17709/")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_m0_probe(endpoint=args.endpoint)
    json_path, markdown_path = write_report(report, args.output)
    print(f"M0 verdict: {report.verdict}")
    print(f"JSON: {json_path.name}")
    print(f"Markdown: {markdown_path.name}")
    return 0 if report.verdict == "PASS_WITH_LIMITS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
