from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import socket
import sys
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .tdxquant import (
    FAILURE_MESSAGES_ZH,
    TdxFailureReason,
    TdxHttpTransport,
    TdxTransportError,
)


class CheckStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    name: str
    status: CheckStatus
    message: str
    reason: TdxFailureReason | None = None


@dataclass(frozen=True, slots=True)
class PreflightReport:
    status: CheckStatus
    platform: str
    python_version: str
    endpoint: str
    checks: tuple[PreflightCheck, ...]
    fund_module: str = "unavailable"
    windows_live_verified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _aggregate(checks: list[PreflightCheck]) -> CheckStatus:
    statuses = {check.status for check in checks}
    if CheckStatus.FAIL in statuses:
        return CheckStatus.FAIL
    if CheckStatus.WARN in statuses:
        return CheckStatus.WARN
    return CheckStatus.PASS


def run_preflight(
    *,
    endpoint: str = "http://127.0.0.1:17709/",
    terminal_path: Path | None = None,
    require_windows: bool = True,
    attempt_api: bool = True,
    timeout_seconds: float = 2.0,
) -> PreflightReport:
    checks: list[PreflightCheck] = []
    TdxHttpTransport(endpoint, timeout_seconds)
    is_windows = sys.platform == "win32"
    checks.append(
        PreflightCheck(
            "operating_system",
            CheckStatus.PASS
            if is_windows
            else (CheckStatus.FAIL if require_windows else CheckStatus.WARN),
            (
                "Windows 环境已就绪。"
                if is_windows
                else "当前不是 Windows；只能验证离线契约，不能证明 TdxQuant 真机可用。"
            ),
        )
    )
    python_supported = (3, 11) <= sys.version_info[:2] <= (3, 12)
    checks.append(
        PreflightCheck(
            "python",
            CheckStatus.PASS if python_supported else CheckStatus.FAIL,
            f"Python {platform.python_version()}（项目要求 3.11 或 3.12）。",
            None if python_supported else TdxFailureReason.DEPENDENCY_MISSING,
        )
    )
    terminal_installed = terminal_path is not None and terminal_path.exists()
    if terminal_path is None:
        checks.append(
            PreflightCheck(
                "terminal_install",
                CheckStatus.WARN,
                "未指定终端路径；请用 -TdxInstallPath 指向官方金融终端安装目录。",
                TdxFailureReason.TERMINAL_NOT_INSTALLED,
            )
        )
    else:
        checks.append(
            PreflightCheck(
                "terminal_install",
                CheckStatus.PASS if terminal_installed else CheckStatus.FAIL,
                (
                    f"已找到终端目录：{terminal_path.name}"
                    if terminal_installed
                    else FAILURE_MESSAGES_ZH[TdxFailureReason.TERMINAL_NOT_INSTALLED]
                ),
                None if terminal_installed else TdxFailureReason.TERMINAL_NOT_INSTALLED,
            )
        )
    python_client = importlib.util.find_spec("tqcenter") is not None
    checks.append(
        PreflightCheck(
            "python_client",
            CheckStatus.PASS if python_client else CheckStatus.WARN,
            (
                "已发现官方 tqcenter Python 客户端。"
                if python_client
                else "未发现 tqcenter；可继续使用官方 127.0.0.1:17709 HTTP 模式。"
            ),
            None if python_client else TdxFailureReason.DEPENDENCY_MISSING,
        )
    )
    host = "127.0.0.1"
    port = 17709
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            pass
    except (OSError, TimeoutError):
        reason = (
            TdxFailureReason.TERMINAL_NOT_RUNNING
            if terminal_installed
            else TdxFailureReason.SERVICE_UNREACHABLE
        )
        checks.append(
            PreflightCheck(
                "tq_service",
                CheckStatus.FAIL if is_windows else CheckStatus.WARN,
                FAILURE_MESSAGES_ZH[reason],
                reason,
            )
        )
        attempt_api = False
    else:
        checks.append(PreflightCheck("tq_service", CheckStatus.PASS, "TQ 本机端口可达。"))
    if attempt_api:
        try:
            raw = TdxHttpTransport(endpoint, timeout_seconds).call(
                "get_stock_list", {"market": "5"}
            )
            has_rows = isinstance(raw, (list, tuple, dict)) and bool(raw)
            if not has_rows:
                raise TdxTransportError(TdxFailureReason.NOT_LOGGED_IN, "empty stock list")
        except TdxTransportError as error:
            checks.append(PreflightCheck("api_session", CheckStatus.FAIL, str(error), error.reason))
        else:
            checks.append(
                PreflightCheck(
                    "api_session",
                    CheckStatus.PASS,
                    "官方股票列表接口可调用；这不代表字段、授权或性能 M0 已通过。",
                )
            )
    return PreflightReport(
        status=_aggregate(checks),
        platform=platform.platform(),
        python_version=platform.python_version(),
        endpoint=endpoint,
        checks=tuple(checks),
        windows_live_verified=(
            is_windows
            and any(
                check.name == "api_session" and check.status is CheckStatus.PASS for check in checks
            )
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the local official TdxQuant runtime")
    parser.add_argument("--endpoint", default="http://127.0.0.1:17709/")
    parser.add_argument("--terminal-path", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-non-windows", action="store_true")
    args = parser.parse_args()
    report = run_preflight(
        endpoint=args.endpoint,
        terminal_path=args.terminal_path,
        require_windows=not args.allow_non_windows,
    )
    rendered = report.to_json()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Preflight: {report.status}; report: {args.output.name}")
    else:
        print(rendered)
    return 0 if report.status is not CheckStatus.FAIL else 2


if __name__ == "__main__":
    raise SystemExit(main())
