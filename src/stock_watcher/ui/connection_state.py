from __future__ import annotations

from enum import StrEnum


class ConnectionState(StrEnum):
    """Provider-neutral connection state displayed by the desktop UI."""

    NOT_APPLICABLE = "不适用"
    CHECKING = "检测中"
    CONNECTED = "已连接"
    DISCONNECTED = "未连接"
