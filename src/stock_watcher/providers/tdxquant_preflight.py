from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import re
import socket
import sys
import uuid
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


REPORT_ENDPOINT = "http://127.0.0.1:17709/"
CANONICAL_CHECK_NAMES = (
    "operating_system",
    "python",
    "terminal_install",
    "python_client",
    "tq_service",
    "api_session",
)
FALLBACK_CHECK_NAMES = ("api_session",)


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

    def __post_init__(self) -> None:
        check_names = tuple(check.name for check in self.checks)
        derived_status = _aggregate(self.checks)
        canonical = check_names == CANONICAL_CHECK_NAMES
        fallback = (
            check_names == FALLBACK_CHECK_NAMES
            and derived_status is CheckStatus.FAIL
        )
        expected_live = canonical and derived_status is CheckStatus.PASS
        report_is_consistent = (
            (canonical or fallback)
            and self.status is derived_status
            and self.windows_live_verified is expected_live
        )
        object.__setattr__(
            self,
            "status",
            derived_status if report_is_consistent else CheckStatus.FAIL,
        )
        object.__setattr__(self, "windows_live_verified", expected_live and report_is_consistent)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _aggregate(checks: tuple[PreflightCheck, ...] | list[PreflightCheck]) -> CheckStatus:
    statuses = {check.status for check in checks}
    if CheckStatus.FAIL in statuses:
        return CheckStatus.FAIL
    if CheckStatus.WARN in statuses:
        return CheckStatus.WARN
    return CheckStatus.PASS


def _safe_platform_name() -> str:
    return "Windows" if sys.platform == "win32" else "non-Windows"


def _failure_report(
    reason: TdxFailureReason = TdxFailureReason.INVALID_RESPONSE,
) -> PreflightReport:
    return PreflightReport(
        status=CheckStatus.FAIL,
        platform=_safe_platform_name(),
        python_version=platform.python_version(),
        endpoint=REPORT_ENDPOINT,
        checks=(
            PreflightCheck(
                "api_session",
                CheckStatus.FAIL,
                FAILURE_MESSAGES_ZH[reason],
                reason,
            ),
        ),
    )


def write_preflight_report(report: PreflightReport, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(report.to_json(), encoding="utf-8")
        json.loads(temporary.read_text(encoding="utf-8"))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


_STOCK_CODE_PATTERN = re.compile(r"\d{6}\.(?:SH|SZ|BJ)", re.IGNORECASE)


def _require_nonempty_stock_list(raw: object) -> None:
    if isinstance(raw, dict):
        known_lists = [raw[key] for key in ("stock_list", "Stocks") if key in raw]
        if len(known_lists) == 1:
            raw = known_lists[0]
        elif raw and all(
            isinstance(code, str) and _STOCK_CODE_PATTERN.fullmatch(code)
            for code in raw
        ):
            raw = tuple(raw)
        else:
            raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE)
    if not isinstance(raw, (list, tuple)):
        raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE)
    if not raw:
        raise TdxTransportError(TdxFailureReason.NOT_LOGGED_IN)
    if not all(
        isinstance(code, str) and _STOCK_CODE_PATTERN.fullmatch(code) for code in raw
    ):
        raise TdxTransportError(TdxFailureReason.INVALID_RESPONSE)


def _check_api_session(endpoint: str, timeout_seconds: float) -> PreflightCheck:
    try:
        raw = TdxHttpTransport(endpoint, timeout_seconds).call(
            "get_stock_list", {"market": "5", "list_type": 0}
        )
        _require_nonempty_stock_list(raw)
    except TdxTransportError as error:
        return PreflightCheck(
            "api_session",
            CheckStatus.FAIL,
            FAILURE_MESSAGES_ZH[error.reason],
            error.reason,
        )
    except Exception:
        # Vendor/runtime details can contain responses, account identifiers, paths,
        # or credentials. Fail closed with a stable category and fixed message.
        reason = TdxFailureReason.INVALID_RESPONSE
        return PreflightCheck(
            "api_session",
            CheckStatus.FAIL,
            FAILURE_MESSAGES_ZH[reason],
            reason,
        )
    return PreflightCheck(
        "api_session",
        CheckStatus.PASS,
        "官方股票列表接口可调用；这不代表字段、授权或性能 M0 已通过。",
    )


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
                    "已找到指定的官方终端目录。"
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
        checks.append(_check_api_session(endpoint, timeout_seconds))
    return PreflightReport(
        status=_aggregate(checks),
        platform=_safe_platform_name(),
        python_version=platform.python_version(),
        endpoint=REPORT_ENDPOINT,
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
    try:
        report = run_preflight(
            endpoint=args.endpoint,
            terminal_path=args.terminal_path,
            require_windows=not args.allow_non_windows,
        )
    except Exception:
        # Runtime/vendor exceptions may contain account identifiers, host names,
        # paths, responses, or credentials. The report keeps a fixed safe schema.
        report = _failure_report()
    rendered = report.to_json()
    if args.output:
        write_preflight_report(report, args.output)
        print(f"Preflight: {report.status}; report: {args.output.name}")
    else:
        print(rendered)
    return 0 if report.status is CheckStatus.PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
