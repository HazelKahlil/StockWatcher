from __future__ import annotations

from datetime import datetime
from pathlib import Path

from stock_watcher.config import DataSourceMode, DataSourceSettings
from stock_watcher.domain import HealthState
from stock_watcher.engine.candidates import CandidateBatch
from stock_watcher.security import (
    FAST_CREDENTIAL,
    SUPER_CREDENTIAL,
    CredentialStore,
    KeyringCredentialStore,
)
from stock_watcher.storage import SQLiteStore

from .connection_state import ConnectionState as TqConnectionState
from .data_source_status import (
    CredentialTester,
    CredentialTestResult,
    NativeRealtimeTester,
    TushareCredentialTester,
    TushareNativeRealtimeTester,
)


class TushareDiagnosticSession:
    """Safe M0 shell. It never emits candidates before the live data gate passes."""

    source_label = "Tushare 兼容 HTTP（真实 M0 尚未完成）"
    phase_label = "Tushare Data Gate · 候选关闭"
    app_badge = "Windows Tushare 数据闸门"
    window_title = "A股观察提醒 · Tushare 数据闸门"
    is_replay = False
    supports_manual_fetch = True
    auto_check_interval_seconds = 60
    connection_name = "数据接口"
    reconnect_label = "检查连接与实时"
    manual_fetch_label = "立即检测实时数据"
    footer_label = "Tushare 兼容 HTTP · 凭据仅存系统安全存储 · 候选关闭"

    def __init__(
        self,
        store_path: Path,
        *,
        credential_store: CredentialStore | None = None,
        tester: CredentialTester | None = None,
        native_realtime_tester: NativeRealtimeTester | None = None,
        settings: DataSourceSettings | None = None,
    ) -> None:
        self.store = SQLiteStore(store_path)
        self.store.initialize()
        self.credential_store = credential_store or KeyringCredentialStore()
        self.tester = tester or TushareCredentialTester(check_super_realtime=False)
        self.native_realtime_tester = (
            native_realtime_tester or TushareNativeRealtimeTester()
        )
        self.settings = settings or DataSourceSettings()
        self.batch: CandidateBatch | None = None
        self.state = HealthState.WARMING
        self.health_detail = "请在“设置 → 数据接口”中测试并保存新换发凭据。"
        self.connection_state = TqConnectionState.DISCONNECTED
        self.connection_detail = "尚未完成 Tushare 凭据与能力 M0。"
        self.data_gate_label = "未就绪"
        self.candidate_gate_label = "关闭"
        self.last_connection_check: datetime | None = None
        self.last_fetch_at: datetime | None = None
        self.last_fetch_detail = "尚未执行真实 M0；不会使用 Mock 结果代替。"
        self.status_issues: tuple[str, ...] = (
            "请设置新换发的超级接口凭据。",
            "全市场、分钟、板块、时间戳和 30 分钟 M0 尚未验证。",
            "真实候选和提醒保持关闭。",
        )

    def provider_changed(self, mode: DataSourceMode) -> None:
        self.settings = self.settings.model_copy(update={"mode": mode})
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.connection_detail = f"已切换到 {mode.value}；旧基线已作废，等待重新预热。"
        self.data_gate_label = "预热中"
        self.candidate_gate_label = "关闭"
        self.last_connection_check = datetime.now().astimezone()
        self.status_issues = ("数据源已变化：必须完成新鲜数据预热后才能放行候选。",)

    def stop(self) -> None:
        self.state = HealthState.STOPPED
        self.connection_state = TqConnectionState.DISCONNECTED
        self.candidate_gate_label = "关闭"

    def warm_and_recover(self) -> None:
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.candidate_gate_label = "关闭"

    def recover(self) -> None:
        self._check_active_profile(manual=False)

    def begin_manual_fetch(self) -> None:
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.candidate_gate_label = "关闭"

    def manual_fetch(self) -> None:
        self._check_active_profile(manual=True)

    def _check_active_profile(self, *, manual: bool) -> None:
        use_fast = self.settings.mode is DataSourceMode.FAST
        profile = self.settings.fast_profile if use_fast else self.settings.super_profile
        reference = FAST_CREDENTIAL if use_fast else SUPER_CREDENTIAL
        now = datetime.now().astimezone()
        if manual:
            self.last_fetch_at = now
        self.last_connection_check = now
        try:
            secret = self.credential_store.get(reference)
        except Exception:
            secret = None
        if not secret:
            self.state = HealthState.WARMING
            self.connection_state = TqConnectionState.DISCONNECTED
            self.connection_detail = "系统安全存储中没有当前接口凭据。"
            self.data_gate_label = "未就绪"
            self.last_fetch_detail = "接口检测未执行：请先在“设置 → 数据接口”保存凭据。"
            self.status_issues = (
                "当前接口凭据不存在或系统安全存储不可用。",
                "真实候选和提醒保持关闭。",
            )
            return

        result = self.tester.test(profile, secret)
        self.state = HealthState.WARMING
        self.candidate_gate_label = "关闭"
        self.connection_state = (
            TqConnectionState.CONNECTED
            if result.success
            else TqConnectionState.DISCONNECTED
        )
        self.connection_detail = result.status_text
        if not result.success:
            self.data_gate_label = "未就绪"
            self.last_fetch_detail = "只读接口检测失败；旧数据不会用于候选。"
            self.status_issues = (
                result.safe_reason or "数据接口检测失败。",
                "真实候选和提醒保持关闭。",
            )
            return
        if (
            result.realtime_status == "available"
            and result.realtime_source_timestamp_present
        ):
            self._apply_realtime_status(result, use_fast=use_fast)
            return
        native_result = self._check_native_realtime()
        if native_result is not None:
            self.connection_detail = (
                f"{result.status_text}；{native_result.status_text}"
            )
            self._apply_realtime_status(
                native_result,
                use_fast=use_fast,
                native_route=True,
            )
            return
        self._apply_realtime_status(result, use_fast=use_fast)

    def _check_native_realtime(self) -> CredentialTestResult | None:
        try:
            secret = self.credential_store.get(FAST_CREDENTIAL)
        except Exception:
            secret = None
        if not secret:
            return None
        return self.native_realtime_tester.test(
            self.settings.native_realtime_profile,
            secret,
        )

    def _apply_realtime_status(
        self,
        result: CredentialTestResult,
        *,
        use_fast: bool,
        native_route: bool = False,
    ) -> None:
        realtime_status = result.realtime_status
        source_timestamp_present = result.realtime_source_timestamp_present
        if use_fast and not native_route:
            self.data_gate_label = "实时未验证"
            self.last_fetch_detail = (
                "基础接口检测通过；快速接口不承担实时主链路，未保存响应正文。"
            )
            self.status_issues = (
                "快速接口实时能力未进入允许列表；实时请求不会自动回退或拼接。",
                "真实候选和提醒保持关闭。",
            )
            return
        if realtime_status == "available" and source_timestamp_present:
            self.data_gate_label = "实时待 M0"
            self.last_fetch_detail = (
                "文档原生实时快照已有数据和供应商时间；未保存响应正文，候选仍关闭。"
                if native_route
                else "实时快照已有数据和供应商时间；未保存响应正文，候选仍关闭。"
            )
            self.status_issues = (
                (
                    "原生实时快照可读；仍需全市场连续 30 分钟 M0、停滞与恢复验证。"
                    if native_route
                    else "实时快照可读；仍需全市场连续 30 分钟 M0、停滞与恢复验证。"
                ),
                "M0 放行前真实候选和提醒保持关闭。",
            )
            return
        if realtime_status == "source_timestamp_missing":
            self.data_gate_label = "实时缺时间戳"
            self.last_fetch_detail = (
                "实时快照有数据但缺可信供应商时间；未保存响应正文。"
            )
            self.status_issues = (
                "接收时间不能冒充供应商时间；无法证明数据新鲜度。",
                "真实候选和提醒保持关闭。",
            )
            return
        self.data_gate_label = "实时不可用"
        safe_labels = {
            "empty_data": "实时日线返回空数据；当前凭据通常尚未开通独立实时权限。",
            "permission_denied": "当前凭据没有实时日线权限。",
            "timeout": "实时接口响应超时。",
            "rate_limited": "实时接口触发频率限制。",
            "business_error": "实时接口返回业务错误。",
            "not_checked": "当前连接测试尚未验证实时接口。",
        }
        self.last_fetch_detail = (
            "基础接口连接通过，但文档原生实时快照不可用；未保存响应正文。"
            if native_route
            else "基础接口连接通过，但实时快照不可用；未保存响应正文。"
        )
        self.status_issues = (
            safe_labels.get(realtime_status, "实时接口未通过严格检测。"),
            "官方实时日线/分钟属于独立权限；真实候选和提醒保持关闭。",
        )
