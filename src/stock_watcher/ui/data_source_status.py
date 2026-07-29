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
    realtime_status: str = "not_checked"
    realtime_records: int = 0
    realtime_source_timestamp_present: bool = False


class CredentialTester(Protocol):
    def test(self, profile: HttpProfile, secret: str) -> CredentialTestResult: ...


@dataclass(slots=True)
class TushareCredentialTester:
    clock: type[datetime] = datetime

    def test(self, profile: HttpProfile, secret: str) -> CredentialTestResult:
        tested_at = self.clock.now().astimezone()
        realtime_status = "not_checked"
        realtime_records = 0
        realtime_source_timestamp_present = False
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
                try:
                    realtime = transport.execute(
                        TransportRequest(
                            endpoint="/tushare/pro/rt_k",
                            api_name="rt_k",
                            params={"ts_code": "3*.SZ,6*.SH,0*.SZ,9*.BJ"},
                            fields=(
                                "ts_code",
                                "pre_close",
                                "close",
                                "vol",
                                "amount",
                                "trade_time",
                            ),
                            method="GET",
                            realtime=True,
                        )
                    )
                except ProviderError as exc:
                    realtime_status = exc.reason.value
                else:
                    realtime_records = len(realtime.records)
                    realtime_source_timestamp_present = (
                        realtime.provenance.source_ts is not None
                    )
                    realtime_status = (
                        "available"
                        if realtime_source_timestamp_present
                        else "source_timestamp_missing"
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
            permission_summary=_permission_summary(profile.name, realtime_status),
            expires_at="服务未返回可验证到期时间",
            realtime_status=realtime_status,
            realtime_records=realtime_records,
            realtime_source_timestamp_present=realtime_source_timestamp_present,
        )


def _permission_summary(profile_name: str, realtime_status: str) -> str:
    if profile_name != "super":
        return "基础调用已验证；快速接口实时能力未进入允许列表"
    if realtime_status == "available":
        return "基础与实时快照有数据；连续稳定性仍以 30 分钟 M0 为准"
    if realtime_status == "source_timestamp_missing":
        return "实时快照有数据但缺可信供应商时间；候选保持关闭"
    safe_labels = {
        "empty_data": "实时快照为空，通常表示实时日线权限未开通或上游无数据",
        "permission_denied": "当前凭据没有实时日线权限",
        "timeout": "实时接口响应超时",
        "rate_limited": "实时接口触发频率限制",
        "business_error": "实时接口返回业务错误",
    }
    label = safe_labels.get(realtime_status, "实时能力尚未验证")
    return f"基础调用已验证；{label}"
