from __future__ import annotations

import ctypes
import sys
from typing import Any

APP_MUTEX_NAME = "StockWatcher.AppMutex"
ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9
MAIN_WINDOW_TITLE = "StockWatcher · 当前观察"

_mutex_handle: Any = None


def acquire_app_mutex() -> bool:
    """Create the Inno Setup mutex. Return True if this process is primary."""
    global _mutex_handle
    if sys.platform != "win32":
        return True
    if _mutex_handle is not None:
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, APP_MUTEX_NAME)
    if not handle:
        return True
    _mutex_handle = handle
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def raise_existing_window() -> bool:
    """Restore the running StockWatcher window when a second process starts."""
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, MAIN_WINDOW_TITLE)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    return True
