from __future__ import annotations

import ctypes
import sys
from typing import Any

APP_MUTEX_NAME = "StockWatcher.AppMutex"

_mutex_handle: Any = None


def acquire_app_mutex() -> Any:
    """Create the named mutex Inno Setup uses to find a running instance."""
    global _mutex_handle
    if sys.platform != "win32":
        return None
    if _mutex_handle is not None:
        return _mutex_handle
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, True, APP_MUTEX_NAME)
    if handle:
        _mutex_handle = handle
    return handle
