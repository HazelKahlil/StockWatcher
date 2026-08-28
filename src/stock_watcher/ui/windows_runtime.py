from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any

APP_MUTEX_NAME = "StockWatcher.AppMutex"
ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9

_mutex_handle: Any = None
_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def acquire_app_mutex() -> bool:
    """Create the Inno Setup mutex. Return True if this process is primary."""
    global _mutex_handle
    if sys.platform != "win32":
        return True
    if _mutex_handle is not None:
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, APP_MUTEX_NAME)
    if not handle:
        return True
    _mutex_handle = handle
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


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
