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

from .data_source_status import CredentialTester, TushareCredentialTester
from .tdx_session import TqConnectionState


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
    reconnect_label = "检查数据接口"
    manual_fetch_label = "立即检测数据接口"
    footer_label = "Tushare 兼容 HTTP · 凭据仅存系统安全存储 · 候选关闭"

    def __init__(
        self,
        store_path: Path,
        *,
        credential_store: CredentialStore | None = None,
        tester: CredentialTester | None = None,
        settings: DataSourceSettings | None = None,
    ) -> None:
        self.store = SQLiteStore(store_path)
        self.store.initialize()
        self.credential_store = credential_store or KeyringCredentialStore()
        self.tester = tester or TushareCredentialTester()
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
        self.data_gate_label = "M0 未完成" if result.success else "未就绪"
        self.last_fetch_detail = (
            "只读接口检测通过；未保存响应正文，真实候选仍关闭。"
            if result.success
            else "只读接口检测失败；旧数据不会用于候选。"
        )
        self.status_issues = (
            (
                "连接已通过；全市场、分钟、板块、时间戳和 30 分钟 M0 尚未验证。",
                "真实候选和提醒保持关闭。",
            )
            if result.success
            else (
                result.safe_reason or "数据接口检测失败。",
                "真实候选和提醒保持关闭。",
            )
        )
