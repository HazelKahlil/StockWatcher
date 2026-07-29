from __future__ import annotations

from enum import StrEnum


class ProviderFailureReason(StrEnum):
    CREDENTIAL_MISSING = "credential_missing"
    CREDENTIAL_INVALID = "credential_invalid"
    PERMISSION_DENIED = "permission_denied"
    EXPIRED = "expired"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"
    TIMEOUT = "timeout"
    SERVER_ERROR = "server_error"
    FRESHNESS = "freshness"
    BUSINESS_ERROR = "business_error"
    INVALID_JSON = "invalid_json"
    SCHEMA_CHANGED = "schema_changed"
    EMPTY_DATA = "empty_data"
    STALE_DATA = "stale_data"
    DATA_ROLLBACK = "data_rollback"


_PUBLIC_MESSAGES = {
    ProviderFailureReason.CREDENTIAL_MISSING: "尚未设置数据接口凭据。",
    ProviderFailureReason.CREDENTIAL_INVALID: "凭据无效，请更新后重试。",
    ProviderFailureReason.PERMISSION_DENIED: "当前凭据权限不足。",
    ProviderFailureReason.EXPIRED: "数据接口权限已过期。",
    ProviderFailureReason.RATE_LIMITED: "接口访问过于频繁，已暂停本轮请求。",
    ProviderFailureReason.NETWORK: "网络连接失败。",
    ProviderFailureReason.TIMEOUT: "接口响应超时。",
    ProviderFailureReason.SERVER_ERROR: "数据服务暂时不可用。",
    ProviderFailureReason.FRESHNESS: "数据尚未闭合或已经过期。",
    ProviderFailureReason.BUSINESS_ERROR: "数据接口返回业务错误。",
    ProviderFailureReason.INVALID_JSON: "数据接口返回了无法解析的内容。",
    ProviderFailureReason.SCHEMA_CHANGED: "数据格式发生变化，已停止产生新候选。",
    ProviderFailureReason.EMPTY_DATA: "数据接口返回空数据。",
    ProviderFailureReason.STALE_DATA: "数据已过期，已停止产生新候选。",
    ProviderFailureReason.DATA_ROLLBACK: "数据时间发生回滚，已停止产生新候选。",
}


class ProviderError(RuntimeError):
    def __init__(
        self,
        reason: ProviderFailureReason,
        *,
        http_status: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(_PUBLIC_MESSAGES[reason])
        self.reason = reason
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds

    @property
    def public_message(self) -> str:
        return _PUBLIC_MESSAGES[self.reason]
