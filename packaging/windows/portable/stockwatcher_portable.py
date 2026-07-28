from __future__ import annotations

import ctypes
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

OFFICIAL_PUBLISHERS = (
    "深圳市财富趋势科技股份有限公司",
    "Shenzhen Fortune Trend Technology",
)
REQUIRED_DEPENDENCIES = {
    "PySide6": ("PySide6", "6.11.1"),
    "pydantic": ("pydantic", "2.13.4"),
    "yaml": ("PyYAML", "6.0.3"),
    "tzdata": ("tzdata", "2026.3"),
    "tqcenter": ("tqcenter", None),
}
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
REPORT_RELATIVE_PATH = Path("StockWatcher") / "reports" / "tdxquant-preflight.json"


class PortableLaunchError(RuntimeError):
    """Expected fail-closed launch error with a fixed, user-safe message."""


@dataclass(frozen=True, slots=True)
class PortableLayout:
    root: Path
    application_src: Path
    preflight: Path
    ui_entry: Path
    project_metadata: Path
    dependency_lock: Path


def portable_layout(script: Path | None = None) -> PortableLayout:
    launcher = (script or Path(__file__)).resolve()
    root = launcher.parents[1]
    application_src = root / "app" / "src"
    package = application_src / "stock_watcher"
    return PortableLayout(
        root=root,
        application_src=application_src,
        preflight=package / "providers" / "tdxquant_preflight.py",
        ui_entry=package / "ui" / "app.py",
        project_metadata=root / "app" / "pyproject.toml",
        dependency_lock=root / "app" / "uv.lock",
    )


def validate_application(layout: PortableLayout) -> None:
    required = (
        layout.application_src / "stock_watcher" / "__init__.py",
        layout.application_src / "stock_watcher" / "__main__.py",
        layout.preflight,
        layout.ui_entry,
        layout.project_metadata,
        layout.dependency_lock,
    )
    if not all(path.is_file() for path in required):
        raise PortableLaunchError(
            "便携包缺少 StockWatcher 应用、原生预检或 UI 入口，程序未启动。"
            "请重新取得并核对完整冻结 ZIP。"
        )


def missing_dependencies(
    finder: Callable[[str], object | None] = importlib.util.find_spec,
    version_getter: Callable[[str], str] = importlib.metadata.version,
) -> tuple[str, ...]:
    problems: list[str] = []
    for module, (distribution, expected) in REQUIRED_DEPENDENCIES.items():
        if finder(module) is None:
            suffix = f" {expected}" if expected else "（官方 TdxQuant 客户端）"
            problems.append(f"{distribution}{suffix}")
            continue
        if expected is not None:
            try:
                actual = version_getter(distribution)
            except importlib.metadata.PackageNotFoundError:
                problems.append(f"{distribution} {expected}")
                continue
            if actual != expected:
                problems.append(f"{distribution} {expected}（当前 {actual}）")
    return tuple(problems)


def _system_executable(relative: str) -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(system_root) / "System32" / relative)


def _powershell_json(command: str, *, environment: dict[str, str] | None = None) -> object:
    completed = subprocess.run(
        [
            _system_executable(r"WindowsPowerShell\v1.0\powershell.exe"),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=8,
        creationflags=CREATE_NO_WINDOW,
        env=environment,
    )
    if completed.returncode != 0:
        return None
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None


def _registry_terminal_candidates() -> tuple[Path, ...]:
    if sys.platform != "win32":
        return ()
    import winreg

    candidates: list[Path] = []
    roots = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
    )
    for hive, key_name in roots:
        try:
            with winreg.OpenKey(hive, key_name) as uninstall:
                for index in range(winreg.QueryInfoKey(uninstall)[0]):
                    try:
                        with winreg.OpenKey(uninstall, winreg.EnumKey(uninstall, index)) as item:
                            display_name = str(
                                winreg.QueryValueEx(item, "DisplayName")[0]
                            ).lower()
                            if not any(
                                marker in display_name
                                for marker in ("通达信", "tdx", "金融终端")
                            ):
                                continue
                            for field in ("InstallLocation", "DisplayIcon"):
                                try:
                                    raw = str(winreg.QueryValueEx(item, field)[0]).strip('" ')
                                except OSError:
                                    continue
                                base = Path(raw.split(",", 1)[0])
                                candidates.append(
                                    base if base.name.lower() == "tdxw.exe" else base / "TdxW.exe"
                                )
                    except OSError:
                        continue
        except OSError:
            continue
    return tuple(dict.fromkeys(candidates))


def _running_terminal_candidates() -> tuple[Path, ...]:
    if sys.platform != "win32":
        return ()
    result = _powershell_json(
        "$currentSession=(Get-Process -Id $PID).SessionId;"
        "$paths=@(Get-Process -Name TdxW -ErrorAction SilentlyContinue"
        "|Where-Object{$_.SessionId -eq $currentSession}"
        "|ForEach-Object{[string]$_.Path});"
        "ConvertTo-Json -InputObject $paths -Compress"
    )
    if isinstance(result, str):
        values: object = [result]
    else:
        values = result
    if not isinstance(values, list):
        return ()
    candidates = (
        Path(value)
        for value in values
        if isinstance(value, str) and value.strip()
    )
    return tuple(dict.fromkeys(candidates))


def _signature_is_official(executable: Path) -> bool:
    if sys.platform != "win32" or not executable.is_file():
        return False
    environment = os.environ.copy()
    environment["STOCKWATCHER_TDX_CANDIDATE"] = str(executable)
    result = _powershell_json(
        "$securityModule=Join-Path $PSHOME "
        "'Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1';"
        "Import-Module -Name $securityModule -ErrorAction Stop;"
        "$s=Get-AuthenticodeSignature -LiteralPath "
        "$env:STOCKWATCHER_TDX_CANDIDATE;"
        "@{status=[string]$s.Status;subject=[string]$s.SignerCertificate.Subject}"
        "|ConvertTo-Json -Compress",
        environment=environment,
    )
    if not isinstance(result, dict) or result.get("status") != "Valid":
        return False
    subject = str(result.get("subject", ""))
    return any(publisher.casefold() in subject.casefold() for publisher in OFFICIAL_PUBLISHERS)


def find_official_terminal() -> Path | None:
    running = _running_terminal_candidates()
    if running:
        if len(running) != 1:
            return None
        return running[0] if _signature_is_official(running[0]) else None
    candidates = list(_registry_terminal_candidates())
    candidates.extend(
        [
            Path(os.environ.get("SystemDrive", "C:")) / "new_tdx" / "TdxW.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "tdx" / "TdxW.exe",
        ]
    )
    return next(
        (candidate for candidate in dict.fromkeys(candidates) if _signature_is_official(candidate)),
        None,
    )


def _load_application_module(
    layout: PortableLayout | None,
    name: str,
) -> ModuleType:
    if not getattr(sys, "frozen", False):
        if layout is None:
            raise PortableLaunchError("无法定位 StockWatcher 应用源码，程序未启动。")
        sys.path.insert(0, str(layout.application_src))
    return __import__(name, fromlist=["*"])


def _report_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise PortableLaunchError("无法定位当前用户的本地数据目录，StockWatcher 未启动。")
    return Path(local_app_data) / REPORT_RELATIVE_PATH


def _strict_preflight_pass(report: object, check_status: Any) -> bool:
    status = getattr(report, "status", None)
    checks = getattr(report, "checks", ())
    api_checks = tuple(check for check in checks if getattr(check, "name", None) == "api_session")
    return (
        status is check_status.PASS
        and getattr(report, "windows_live_verified", False) is True
        and len(api_checks) == 1
        and getattr(api_checks[0], "status", None) is check_status.PASS
    )


def run_native_preflight(
    layout: PortableLayout | None,
    *,
    terminal: Path | None = None,
) -> bool:
    module = _load_application_module(
        layout, "stock_watcher.providers.tdxquant_preflight"
    )
    report = module.run_preflight(terminal_path=terminal)
    module.write_preflight_report(report, _report_path())
    return _strict_preflight_pass(report, module.CheckStatus)


def launch_stockwatcher_ui(layout: PortableLayout | None) -> int:
    app = _load_application_module(layout, "stock_watcher.ui.app")
    sys.argv = ["StockWatcher", "--provider", "tdxquant"]
    return int(app.run())


class _SingleInstance:
    def __init__(self) -> None:
        self._handle: int | None = None

    def acquire(self) -> bool:
        if sys.platform != "win32":
            return True
        kernel32 = ctypes.windll.kernel32
        self._handle = int(
            kernel32.CreateMutexW(None, False, "Local\\StockWatcherInternalPortable")
        )
        return bool(self._handle) and kernel32.GetLastError() != 183


def _message_box(text: str, title: str = "StockWatcher") -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)


def _dependency_message(missing: tuple[str, ...]) -> str:
    joined = "；".join(missing)
    return (
        "目标机缺少 StockWatcher 运行依赖，程序未启动。\n\n"
        f"缺少：{joined}。\n"
        "请由管理员按《第一次使用》中的锁定前提离线准备；本入口不会联网安装依赖。"
    )


def launch_once(layout: PortableLayout | None = None) -> int:
    frozen = bool(getattr(sys, "frozen", False))
    if not frozen and (sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32):
        raise PortableLaunchError(
            "本便携候选只允许 64 位 Python 3.12 Pythonw，StockWatcher 未启动。"
        )
    resolved_layout = None if frozen else (layout or portable_layout())
    if not frozen:
        assert resolved_layout is not None
        validate_application(resolved_layout)
        missing = missing_dependencies()
        if missing:
            raise PortableLaunchError(_dependency_message(missing))
    terminal = find_official_terminal()
    if terminal is None:
        raise PortableLaunchError(
            "未找到数字签名有效且发布者匹配官方公司的通达信终端。"
            "StockWatcher 未启动。请由本人通过官方终端的正常入口启动并登录后重试。"
        )
    if not run_native_preflight(resolved_layout, terminal=terminal):
        raise PortableLaunchError(
            "原生 TdxQuant 预检未通过，StockWatcher 候选界面未启动。"
            "本入口不会自动启动终端或请求管理员权限。"
            "请由本人通过官方终端的正常入口完成启动、登录并开启 TQ 后，"
            "再双击主入口重试。"
        )
    return launch_stockwatcher_ui(resolved_layout)


def main() -> int:
    instance = _SingleInstance()
    if not instance.acquire():
        _message_box("StockWatcher 已经在运行。", "StockWatcher")
        return 0
    try:
        return launch_once()
    except PortableLaunchError as error:
        _message_box(str(error))
        return 2
    except Exception:
        _message_box(
            "StockWatcher 启动检查失败，程序未启动。"
            "请重新核对完整 ZIP、运行前提和原生预检报告。"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
