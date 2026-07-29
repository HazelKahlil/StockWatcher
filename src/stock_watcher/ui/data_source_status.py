from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from stock_watcher.config import HttpProfile
from stock_watcher.providers.tushare.errors import ProviderError
from stock_watcher.providers.tushare.fast_transport import FastTransport
from stock_watcher.providers.tushare.http_transport import BaseHttpTransport
from stock_watcher.providers.tushare.super_transport import SuperTransport
from stock_watcher.providers.tushare.transport_protocol import TransportRequest


@dataclass(frozen=True, slots=True)
class CredentialTestResult:
    success: bool
    tested_at: datetime
    status_text: str
    permission_summary: str
    expires_at: str
    safe_reason: str | None = None


class CredentialTester(Protocol):
    def test(self, profile: HttpProfile, secret: str) -> CredentialTestResult: ...


@dataclass(slots=True)
class TushareCredentialTester:
    clock: type[datetime] = datetime

    def test(self, profile: HttpProfile, secret: str) -> CredentialTestResult:
        tested_at = self.clock.now().astimezone()
        try:
            if profile.name == "super":
                transport: BaseHttpTransport = SuperTransport(profile, lambda: secret)
                result = transport.execute(
                    TransportRequest(
                        endpoint="/tushare/pro/trade_cal",
                        api_name="trade_cal",
                        params={
                            "exchange": "SSE",
                            "start_date": "20260301",
                            "end_date": "20260303",
                        },
                        fields=("exchange", "cal_date", "is_open"),
                        method="GET",
                    )
                )
            else:
                transport = FastTransport(profile, lambda: secret)
                result = transport.execute(
                    TransportRequest(
                        endpoint="/",
                        api_name="trade_cal",
                        params={"exchange": "SSE"},
                        fields=("exchange", "cal_date", "is_open"),
                        allow_empty=True,
                    )
                )
        except ProviderError as exc:
            return CredentialTestResult(
                success=False,
                tested_at=tested_at,
                status_text=exc.public_message,
                permission_summary="未取得权限摘要",
                expires_at="未知",
                safe_reason=exc.reason.value,
            )
        return CredentialTestResult(
            success=True,
            tested_at=tested_at,
            status_text=f"连接测试通过（HTTP {result.http_status}）",
            permission_summary="基础调用已验证；完整权限以 M0 为准",
            expires_at="服务未返回可验证到期时间",
        )
