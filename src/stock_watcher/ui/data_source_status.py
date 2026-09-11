from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from stock_watcher.config import HttpProfile, NativeRealtimeProfile
from stock_watcher.providers.tushare.errors import ProviderError, ProviderFailureReason
from stock_watcher.providers.tushare.fast_transport import FastTransport
from stock_watcher.providers.tushare.http_transport import BaseHttpTransport
from stock_watcher.providers.tushare.native_realtime_transport import (
    NativeRealtimeTransport,
)
from stock_watcher.providers.tushare.rate_limit import ApplicationRequestBudget
from stock_watcher.providers.tushare.sdk_pro_transport import TushareSdkProTransport
from stock_watcher.providers.tushare.super_transport import SuperTransport
from stock_watcher.providers.tushare.transport_protocol import TransportRequest

VERIFIED = "verified"
PENDING_VERIFICATION = "pending_verification"
CREDENTIAL_INVALID = "credential_invalid"
PERMISSION_DENIED = "permission_denied"
FAILED = "failed"
NOT_TESTED = "not_tested"


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
    realtime_route: str = "super_rt_k"
    verification_state: str = NOT_TESTED


def credential_verification_state(result: CredentialTestResult) -> str:
    """Map a test result to an explicit verification state.

    ``success`` only means the UI may offer a save confirmation.  It is not
    proof that the Token passed authentication or permission checks.
    """
    if result.verification_state and result.verification_state != NOT_TESTED:
        return result.verification_state
    if result.success:
        return VERIFIED
    reason = result.safe_reason or ""
    if reason == ProviderFailureReason.CREDENTIAL_INVALID.value:
        return CREDENTIAL_INVALID
    if reason == ProviderFailureReason.PERMISSION_DENIED.value:
        return PERMISSION_DENIED
    return FAILED


class CredentialTester(Protocol):
    def test(self, profile: HttpProfile, secret: str) -> CredentialTestResult: ...


class NativeRealtimeTester(Protocol):
    def test(
        self,
        profile: NativeRealtimeProfile,
        secret: str,
    ) -> CredentialTestResult: ...


@dataclass(slots=True)
class TushareCredentialTester:
    clock: type[datetime] = datetime
    check_super_realtime: bool = True

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
                if self.check_super_realtime:
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
                verification_state=credential_verification_state(
                    CredentialTestResult(
                        success=False,
                        tested_at=tested_at,
                        status_text=exc.public_message,
                        permission_summary="未取得权限摘要",
                        expires_at="未知",
                        safe_reason=exc.reason.value,
                    )
                ),
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
            verification_state=VERIFIED,
        )


@dataclass(slots=True)
class TushareNativeRealtimeTester:
    """One-security availability probe for the explicitly approved SDK route."""

    clock: type[datetime] = datetime
    request_budget: ApplicationRequestBudget | None = None

    def test(
        self,
        profile: NativeRealtimeProfile,
        secret: str,
    ) -> CredentialTestResult:
        tested_at = self.clock.now().astimezone()
        try:
            transport = NativeRealtimeTransport(
                profile,
                lambda: secret,
                request_budget=self.request_budget,
            )
            result = transport.execute(
                TransportRequest(
                    endpoint="realtime_quote",
                    api_name="realtime_quote",
                    params={"ts_code": "000001.SZ"},
                    fields=(
                        "ts_code",
                        "pre_close",
                        "price",
                        "vol",
                        "amount",
                        "source_ts",
                    ),
                    realtime=True,
                )
            )
        except ProviderError as exc:
            failed = CredentialTestResult(
                success=False,
                tested_at=tested_at,
                status_text="文档原生实时检测未通过",
                permission_summary="原生实时快照尚不可用",
                expires_at="服务未返回可验证到期时间",
                safe_reason=exc.reason.value,
                realtime_status=exc.reason.value,
                realtime_route="native_realtime",
            )
            return CredentialTestResult(
                success=False,
                tested_at=tested_at,
                status_text=failed.status_text,
                permission_summary=failed.permission_summary,
                expires_at=failed.expires_at,
                safe_reason=failed.safe_reason,
                realtime_status=failed.realtime_status,
                realtime_route=failed.realtime_route,
                verification_state=credential_verification_state(failed),
            )
        except Exception:
            return CredentialTestResult(
                success=False,
                tested_at=tested_at,
                status_text="文档原生实时检测未通过",
                permission_summary="原生实时运行依赖不可用",
                expires_at="服务未返回可验证到期时间",
                safe_reason="business_error",
                realtime_status="business_error",
                realtime_route="native_realtime",
                verification_state=FAILED,
            )
        timestamp_present = result.provenance.source_ts is not None
        return CredentialTestResult(
            success=True,
            tested_at=tested_at,
            status_text="文档原生实时接口有数据",
            permission_summary=(
                "原生实时快照有供应商时间；连续稳定性仍以 30 分钟 M0 为准"
                if timestamp_present
                else "原生实时快照缺可信供应商时间"
            ),
            expires_at="服务未返回可验证到期时间",
            realtime_status=(
                "available" if timestamp_present else "source_timestamp_missing"
            ),
            realtime_records=len(result.records),
            realtime_source_timestamp_present=timestamp_present,
            realtime_route="native_realtime",
            verification_state=VERIFIED,
        )


@dataclass(slots=True)
class LightweightCredentialTester:
    """Validate a candidate Token with one Pro call before an atomic save.

    A 429 is not a bad Token, and it is also not proof that the Token works.
    Ordinary Pro can stay rate-limited while the approved native realtime
    route still works.  Explicit auth or permission failures stay failures.
    """

    clock: type[datetime] = datetime
    request_budget: ApplicationRequestBudget | None = None
    realtime_profile: NativeRealtimeProfile | None = None
    native_realtime_tester: NativeRealtimeTester | None = None

    def test(self, profile: HttpProfile, secret: str) -> CredentialTestResult:
        if profile.name != "tushare_15000":
            return TushareCredentialTester(clock=self.clock).test(profile, secret)
        tested_at = self.clock.now().astimezone()
        if self.request_budget is not None:
            remaining = self.request_budget.cooldown_remaining(lane="pro")
            if remaining > 0:
                return self._resolve_rate_limited_token(
                    secret,
                    tested_at,
                    pro_tested=False,
                )
        start = tested_at - timedelta(days=7)
        try:
            result = TushareSdkProTransport(
                profile,
                lambda: secret,
                request_budget=self.request_budget,
            ).execute(
                TransportRequest(
                    endpoint="/",
                    api_name="trade_cal",
                    params={
                        "exchange": "SSE",
                        "start_date": start.strftime("%Y%m%d"),
                        "end_date": tested_at.strftime("%Y%m%d"),
                    },
                    fields=("exchange", "cal_date", "is_open"),
                    allow_empty=True,
                )
            )
        except ProviderError as exc:
            if exc.reason is ProviderFailureReason.RATE_LIMITED:
                return self._resolve_rate_limited_token(
                    secret,
                    tested_at,
                    pro_tested=True,
                )
            state = credential_verification_state(
                CredentialTestResult(
                    success=False,
                    tested_at=tested_at,
                    status_text=exc.public_message,
                    permission_summary="基础连接未通过；当前 Token 未被替换。",
                    expires_at="未知",
                    safe_reason=exc.reason.value,
                )
            )
            return CredentialTestResult(
                success=False,
                tested_at=tested_at,
                status_text=exc.public_message,
                permission_summary="基础连接未通过；当前 Token 未被替换。",
                expires_at="未知",
                safe_reason=exc.reason.value,
                verification_state=state,
            )
        return CredentialTestResult(
            success=True,
            tested_at=tested_at,
            status_text=f"基础连接测试通过（HTTP {result.http_status}），可安全保存 Token。",
            permission_summary="股票列表、板块、历史分钟和实时行情将在后台分项检测。",
            expires_at="服务未返回可验证到期时间",
            realtime_route="native_realtime",
            verification_state=VERIFIED,
        )

    def _resolve_rate_limited_token(
        self,
        secret: str,
        tested_at: datetime,
        *,
        pro_tested: bool,
    ) -> CredentialTestResult:
        if self.request_budget is not None:
            remaining = self.request_budget.cooldown_remaining(lane="realtime")
            if remaining > 0:
                return self._pending_rate_limited_result(
                    tested_at,
                    realtime_status=ProviderFailureReason.RATE_LIMITED.value,
                    pro_tested=pro_tested,
                    realtime_checked=False,
                )
        realtime = self._native_realtime_result(secret)
        realtime_state = credential_verification_state(realtime)
        if realtime_state == CREDENTIAL_INVALID:
            return CredentialTestResult(
                success=False,
                tested_at=tested_at,
                status_text="认证失败，当前 Token 未被替换。",
                permission_summary="实时接口拒绝了这组凭据。限流不能证明 Token 有效。",
                expires_at="未知",
                safe_reason=ProviderFailureReason.CREDENTIAL_INVALID.value,
                realtime_status=realtime.realtime_status,
                realtime_route="native_realtime",
                verification_state=CREDENTIAL_INVALID,
            )
        if realtime_state == PERMISSION_DENIED:
            return CredentialTestResult(
                success=False,
                tested_at=tested_at,
                status_text="当前 Token 缺少实时接口权限，未被替换。",
                permission_summary="这只说明 realtime_quote 权限不足，不代表全部接口都不可用。",
                expires_at="未知",
                safe_reason=ProviderFailureReason.PERMISSION_DENIED.value,
                realtime_status=realtime.realtime_status,
                realtime_route="native_realtime",
                verification_state=PERMISSION_DENIED,
            )
        if realtime.success:
            return CredentialTestResult(
                success=True,
                tested_at=tested_at,
                status_text="基础接口限流，原生实时可用，可安全保存 Token。",
                permission_summary="限流不是 Token 无效。保存后基础数据将在冷却结束后后台检测。",
                expires_at="未知",
                safe_reason=ProviderFailureReason.RATE_LIMITED.value,
                realtime_status=realtime.realtime_status,
                realtime_records=realtime.realtime_records,
                realtime_source_timestamp_present=realtime.realtime_source_timestamp_present,
                realtime_route="native_realtime",
                verification_state=VERIFIED,
            )
        return self._pending_rate_limited_result(
            tested_at,
            realtime_status=realtime.realtime_status,
            pro_tested=pro_tested,
            realtime_checked=True,
        )

    def _native_realtime_result(self, secret: str) -> CredentialTestResult:
        tester = self.native_realtime_tester or TushareNativeRealtimeTester(
            clock=self.clock,
            request_budget=self.request_budget,
        )
        return tester.test(self.realtime_profile or NativeRealtimeProfile(), secret)

    def _pending_rate_limited_result(
        self,
        tested_at: datetime,
        *,
        realtime_status: str,
        pro_tested: bool,
        realtime_checked: bool,
    ) -> CredentialTestResult:
        if pro_tested:
            status_text = "基础接口限流，Token 尚未验证通过，可确认保存为待验证。"
        else:
            status_text = "基础接口仍在冷却，新 Token 尚未完成基础验证，可确认保存为待验证。"
        if realtime_checked:
            permission = (
                "限流既不能证明 Token 无效，也不能证明它有效。"
                "已有可用 Token 不会被未验证值替换。"
            )
        else:
            permission = "实时通道也在冷却，未绕过请求预算。已有可用 Token 不会被未验证值替换。"
        return CredentialTestResult(
            success=True,
            tested_at=tested_at,
            status_text=status_text,
            permission_summary=permission,
            expires_at="未知",
            safe_reason=ProviderFailureReason.RATE_LIMITED.value,
            realtime_status=realtime_status,
            realtime_route="native_realtime",
            verification_state=PENDING_VERIFICATION,
        )


class TusharePrimaryCredentialTester(LightweightCredentialTester):
    """Backward-compatible name for the one-call lightweight tester."""


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
