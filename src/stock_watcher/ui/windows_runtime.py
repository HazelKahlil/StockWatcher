from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any

APP_MUTEX_NAME = "StockWatcher.AppMutex"
ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9

_mutex_handle: Any = None
_instance_lock_path: Path | None = None
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _instance_lock_file() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(root) / "StockWatcher" / "runtime" / "instance.lock"


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _create_named_mutex() -> None:
    """Keep the mutex Inno Setup uses to detect a running instance."""
    global _mutex_handle
    if _mutex_handle is not None or sys.platform != "win32":
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, APP_MUTEX_NAME)
    if handle:
        _mutex_handle = handle


def acquire_app_mutex() -> bool:
    """Return True if this process is the primary instance."""
    global _instance_lock_path
    _create_named_mutex()
    if sys.platform != "win32":
        return True
    path = _instance_lock_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = int(path.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            existing = 0
        if _pid_is_alive(existing) and existing != os.getpid():
            return False
    path.write_text(str(os.getpid()), encoding="utf-8")
    _instance_lock_path = path
    return True


def raise_existing_window() -> bool:
    """Restore a visible StockWatcher window owned by another process."""
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    found: list[int] = []

    def _callback(hwnd: int, _lparam: int) -> bool:
        length = int(user32.GetWindowTextLengthW(hwnd))
        if length <= 0 or not user32.IsWindowVisible(hwnd):
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if "StockWatcher" not in title and "A股观察提醒" not in title:
            return True
        found.append(int(hwnd))
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return False

    user32.EnumWindows(_WNDENUMPROC(_callback), 0)
    return bool(found)
