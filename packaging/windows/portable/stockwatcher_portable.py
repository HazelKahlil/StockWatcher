from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

ENDPOINT = "http://127.0.0.1:17709/"
TQ_REQUEST = {"id": 1, "method": "get_stock_list", "params": {"market": "5", "list_type": 0}}
OFFICIAL_PUBLISHERS = (
    "深圳市财富趋势科技股份有限公司",
    "Shenzhen Fortune Trend Technology",
)
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
STOCK_CODE_PATTERN = re.compile(r"\d{6}\.(?:SH|SZ|BJ)", re.IGNORECASE)


class PortableState(StrEnum):
    CHECKING = "checking"
    TERMINAL_STARTED = "terminal_started"
    SERVICE_UNAVAILABLE = "service_unavailable"
    API_REJECTED = "api_rejected"
    CONNECTED = "connected"


STATE_TEXT: dict[PortableState, tuple[str, str]] = {
    PortableState.CHECKING: ("正在检测", "正在检查官方通达信和 TQ 本机服务，请稍候。"),
    PortableState.TERMINAL_STARTED: (
        "等待本人登录",
        "已启动验签通过的官方通达信。请在官方终端内由本人完成登录，然后点“重新检测”。",
    ),
    PortableState.SERVICE_UNAVAILABLE: (
        "TQ 尚未连接",
        "未检测到可用的 TQ 本机服务。请确认官方通达信已登录并开启 TQ，然后重新检测。",
    ),
    PortableState.API_REJECTED: (
        "TQ 检查未通过",
        "TQ 已响应，但最小只读检查未成功。请检查终端登录、版本和权限，然后重新检测。",
    ),
    PortableState.CONNECTED: (
        "TQ 已连接",
        "最小只读检查已通过。StockWatcher 已打开；真实字段 M0 完成前不会生成新候选。",
    ),
}


@dataclass(frozen=True, slots=True)
class ProbeResult:
    state: PortableState

    @property
    def connected(self) -> bool:
        return self.state is PortableState.CONNECTED


def _system_executable(relative: str) -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(system_root) / "System32" / relative)


def _extract_stock_list(payload: object) -> object:
    if not isinstance(payload, dict):
        raise ValueError("invalid response")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("invalid result")
    if str(result.get("ErrorId", "0")) not in {"", "0", "None"}:
        raise PermissionError("vendor rejected request")
    value = result.get("Value", result)
    if isinstance(value, dict):
        if "stock_list" in value:
            value = value["stock_list"]
        elif "Stocks" in value:
            value = value["Stocks"]
        elif value and all(
            isinstance(code, str) and STOCK_CODE_PATTERN.fullmatch(code) for code in value
        ):
            value = list(value)
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("empty stock list")
    if not all(
        isinstance(code, str) and STOCK_CODE_PATTERN.fullmatch(code) for code in value
    ):
        raise ValueError("invalid stock list")
    return value


def probe_tq(
    *,
    endpoint: str = ENDPOINT,
    timeout_seconds: float = 2.0,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> ProbeResult:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(TQ_REQUEST, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=timeout_seconds) as response:  # type: ignore[attr-defined]
            payload = json.loads(response.read().decode("utf-8"))
        _extract_stock_list(payload)
    except PermissionError:
        return ProbeResult(PortableState.API_REJECTED)
    except (
        OSError,
        TimeoutError,
        urllib.error.URLError,
        urllib.error.HTTPError,
    ):
        return ProbeResult(PortableState.SERVICE_UNAVAILABLE)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        return ProbeResult(PortableState.API_REJECTED)
    return ProbeResult(PortableState.CONNECTED)


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


def _running_terminal() -> bool:
    if sys.platform != "win32":
        return False
    completed = subprocess.run(
        [_system_executable("tasklist.exe"), "/FI", "IMAGENAME eq TdxW.exe", "/NH"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
        creationflags=CREATE_NO_WINDOW,
    )
    return completed.returncode == 0 and "tdxw.exe" in completed.stdout.lower()


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
                count = winreg.QueryInfoKey(uninstall)[0]
                for index in range(count):
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


def _signature_is_official(executable: Path) -> bool:
    if sys.platform != "win32" or not executable.is_file():
        return False
    environment = os.environ.copy()
    environment["STOCKWATCHER_TDX_CANDIDATE"] = str(executable)
    result = _powershell_json(
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
    candidates = list(_registry_terminal_candidates())
    candidates.extend(
        [
            Path(os.environ.get("SystemDrive", "C:")) / "new_tdx" / "TdxW.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "tdx" / "TdxW.exe",
        ]
    )
    for candidate in dict.fromkeys(candidates):
        if _signature_is_official(candidate):
            return candidate
    return None


def attempt_start_official_terminal() -> bool:
    if _running_terminal():
        return False
    executable = find_official_terminal()
    if executable is None:
        return False
    subprocess.Popen(
        [str(executable)],
        cwd=str(executable.parent),
        close_fds=True,
        creationflags=CREATE_NO_WINDOW,
    )
    return True


def create_desktop_shortcut(entry: Path) -> bool:
    if sys.platform != "win32":
        return False
    environment = os.environ.copy()
    environment["STOCKWATCHER_ENTRY"] = str(entry)
    result = _powershell_json(
        "$desktop=[Environment]::GetFolderPath('Desktop');"
        "$shell=New-Object -ComObject WScript.Shell;"
        "$shortcut=$shell.CreateShortcut((Join-Path $desktop '启动 StockWatcher.lnk'));"
        "$shortcut.TargetPath=(Join-Path $env:WINDIR 'System32\\wscript.exe');"
        "$shortcut.Arguments='\"'+$env:STOCKWATCHER_ENTRY+'\"';"
        "$shortcut.WorkingDirectory=(Split-Path $env:STOCKWATCHER_ENTRY);"
        "$shortcut.Description='启动 StockWatcher';$shortcut.Save();"
        "'true'|ConvertTo-Json -Compress",
        environment=environment,
    )
    return result is True


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


def run_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        _message_box("官方 Python 3.12 缺少桌面组件（Tcl/Tk），StockWatcher 未启动。")
        return 2

    instance = _SingleInstance()
    if not instance.acquire():
        _message_box("StockWatcher 已经在运行。", "StockWatcher")
        return 0

    root = tk.Tk()
    root.title("StockWatcher · 官方 TQ 连接检查")
    root.geometry("620x390")
    root.minsize(560, 360)
    root.configure(bg="#f5f7fb")

    title = tk.Label(
        root,
        text="StockWatcher",
        font=("Microsoft YaHei UI", 24, "bold"),
        bg="#f5f7fb",
        fg="#142235",
    )
    title.pack(anchor="w", padx=34, pady=(30, 2))
    subtitle = tk.Label(
        root,
        text="内部便携版 · 只读连接检查",
        font=("Microsoft YaHei UI", 10),
        bg="#f5f7fb",
        fg="#748296",
    )
    subtitle.pack(anchor="w", padx=36)

    card = tk.Frame(root, bg="#ffffff", highlightbackground="#d9e1ec", highlightthickness=1)
    card.pack(fill="both", expand=True, padx=34, pady=22)
    status = tk.Label(
        card,
        text="",
        font=("Microsoft YaHei UI", 18, "bold"),
        bg="#ffffff",
        fg="#b87700",
    )
    status.pack(anchor="w", padx=24, pady=(24, 8))
    detail = tk.Label(
        card,
        text="",
        font=("Microsoft YaHei UI", 11),
        bg="#ffffff",
        fg="#405067",
        wraplength=520,
        justify="left",
    )
    detail.pack(anchor="w", padx=24)
    safety = tk.Label(
        card,
        text="候选生成：关闭　｜　资金模块：未就绪　｜　交易能力：无",
        font=("Microsoft YaHei UI", 10),
        bg="#ffffff",
        fg="#7d8999",
    )
    safety.pack(anchor="w", padx=24, pady=(18, 10))

    button_row = tk.Frame(card, bg="#ffffff")
    button_row.pack(anchor="w", padx=20, pady=(4, 20))
    retry = tk.Button(button_row, text="重新检测", font=("Microsoft YaHei UI", 10))
    retry.pack(side="left", padx=4)
    shortcut = tk.Button(button_row, text="创建桌面快捷方式", font=("Microsoft YaHei UI", 10))
    shortcut.pack(side="left", padx=4)

    def apply_state(state: PortableState) -> None:
        heading, explanation = STATE_TEXT[state]
        status.configure(
            text=heading,
            fg="#178a4c" if state is PortableState.CONNECTED else "#b45f06",
        )
        detail.configure(text=explanation)
        retry.configure(state="normal")
        if state is PortableState.CONNECTED:
            root.title("StockWatcher · TQ 已连接（候选保持关闭）")

    def finish_probe(result: ProbeResult) -> None:
        if result.state is PortableState.SERVICE_UNAVAILABLE:
            started = attempt_start_official_terminal()
            apply_state(
                PortableState.TERMINAL_STARTED if started else PortableState.SERVICE_UNAVAILABLE
            )
        else:
            apply_state(result.state)

    def check() -> None:
        retry.configure(state="disabled")
        apply_state(PortableState.CHECKING)
        retry.configure(state="disabled")

        def worker() -> None:
            result = probe_tq()
            root.after(0, finish_probe, result)

        threading.Thread(target=worker, daemon=True).start()

    def make_shortcut() -> None:
        entry = Path(__file__).resolve().parents[1] / "启动 StockWatcher.vbs"
        if create_desktop_shortcut(entry):
            messagebox.showinfo("StockWatcher", "桌面快捷方式已创建。")
        else:
            messagebox.showerror("StockWatcher", "未能创建快捷方式；程序和系统设置均未更改。")

    retry.configure(command=check)
    shortcut.configure(command=make_shortcut)
    root.after(100, check)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_gui())
