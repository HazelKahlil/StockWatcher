from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from stock_watcher.config import DataSourceMode
from stock_watcher.domain import SHANGHAI, HealthState
from stock_watcher.engine.candidates import CandidateBatch
from stock_watcher.providers.tdxquant import (
    FAILURE_MESSAGES_ZH,
    TdxFailureReason,
    TdxHttpTransport,
    TdxQuantProvider,
    TdxTransportError,
)
from stock_watcher.providers.tdxquant_preflight import CheckStatus, run_preflight
from stock_watcher.storage import SQLiteStore


class TqConnectionState(StrEnum):
    NOT_APPLICABLE = "不适用"
    CHECKING = "检测中"
    CONNECTED = "已连接"
    DISCONNECTED = "未连接"


_CHECK_LABELS = {
    "operating_system": "Windows 环境",
    "python": "运行环境",
    "terminal_install": "官方终端",
    "python_client": "只读客户端",
    "tq_service": "TQ 本机服务",
    "api_session": "TQ 只读 API",
}

_DATA_GATE_ISSUES = (
    "分钟历史：尚未通过真实交易时段严格门，不能用日线替代。",
    "源时间戳：官方接口尚无已验证的秒级时间，无法证明数据新鲜度。",
    "M0：权威 30 分钟现场验证尚未完成。",
)


class TdxDiagnosticSession:
    """Safe UI session before Windows M0 authorizes candidate production."""

    source_label = "官方通达信 TdxQuant（只读预检）"
    phase_label = "Windows 现场验证"
    app_badge = "Windows TQ 只读版"
    window_title = "A股观察提醒 · Windows TQ 只读版"
    is_replay = False
    supports_manual_fetch = True
    auto_check_interval_seconds = 60
    connection_name = "TQ "
    reconnect_label = "重新连接 TQ"
    manual_fetch_label = "立即抓取（只读）"
    footer_label = "官方 TdxQuant · 本机只读 · 原始响应不显示、不保存"

    def __init__(
        self,
        store_path: Path,
        endpoint: str,
        *,
        terminal_path: Path | None = None,
        preflight_verified: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = SQLiteStore(store_path)
        self.store.initialize()
        self.endpoint = endpoint
        self.terminal_path = terminal_path
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self.batch: CandidateBatch | None = None
        self.state = HealthState.WARMING
        self.health_detail = "正在自动检查 TQ 连接。"
        self.connection_state = TqConnectionState.CHECKING
        self.connection_detail = "正在执行本机端口与官方只读 API 检查。"
        self.data_gate_label = "检查中"
        self.candidate_gate_label = "关闭"
        self.last_connection_check: datetime | None = None
        self.last_fetch_at: datetime | None = None
        self.last_fetch_detail = "尚未人工抓取。"
        self.status_issues: tuple[str, ...] = ()
        if preflight_verified:
            if terminal_path is None:
                raise ValueError("verified TQ preflight requires an official terminal path")
            self._mark_preflight_verified()
        else:
            self.recover()

    def stop(self) -> None:
        self.state = HealthState.STOPPED
        self.health_detail = "用户已暂停实时观察；恢复前不会产生新候选。"
        self.data_gate_label = "已暂停"
        self.candidate_gate_label = "关闭"
        self.status_issues = (
            "实时观察：已由用户暂停。",
            *_DATA_GATE_ISSUES,
        )

    def warm_and_recover(self) -> None:
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.connection_detail = "正在重新执行本机端口与官方只读 API 检查。"
        self.health_detail = "正在重新检测 TQ；检测完成前候选保持关闭。"
        self.data_gate_label = "检测中"
        self.candidate_gate_label = "关闭"
        self.status_issues = ()

    def begin_manual_fetch(self) -> None:
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.connection_detail = "正在执行一次官方 TQ 只读抓取。"
        self.health_detail = "人工只读抓取进行中；候选保持关闭。"
        self.data_gate_label = "检测中"
        self.candidate_gate_label = "关闭"
        self.status_issues = ()

    def provider_changed(self, mode: DataSourceMode) -> None:
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.connection_detail = f"数据源设置已切换为 {mode.value}；等待新数据预热。"
        self.health_detail = "数据源发生变化；旧实时基线不再用于候选。"
        self.data_gate_label = "预热中"
        self.candidate_gate_label = "关闭"
        self.status_issues = ("数据源已变化：候选门已关闭。",)

    def recover(self) -> None:
        checked_at = self._clock()
        try:
            report = run_preflight(
                endpoint=self.endpoint,
                terminal_path=self.terminal_path,
            )
        except Exception:
            self._mark_connection_failure(
                checked_at,
                "TQ 检测发生无法识别的错误，已安全停止候选输出。",
                "TQ 检测：未能完成固定检查集合。",
            )
            return
        self.last_connection_check = checked_at
        if report.status is not CheckStatus.PASS:
            issues = tuple(
                f"{_CHECK_LABELS.get(check.name, check.name)}：{check.message}"
                for check in report.checks
                if check.status is not CheckStatus.PASS
            )
            failure = issues[0] if issues else "TQ 严格检测未通过。"
            self._mark_connection_failure(
                checked_at,
                failure,
                *issues,
            )
            return
        self._mark_preflight_verified(checked_at=checked_at)

    def manual_fetch(self) -> None:
        attempted_at = self._clock()
        self.last_fetch_at = attempted_at
        try:
            provider = TdxQuantProvider(
                transport=TdxHttpTransport(self.endpoint, timeout_seconds=5.0)
            )
            securities = provider.stock_list("5")
            if not securities:
                raise TdxTransportError(TdxFailureReason.NOT_LOGGED_IN)
        except TdxTransportError as error:
            message = FAILURE_MESSAGES_ZH[error.reason]
            self.last_fetch_detail = f"人工只读抓取失败：{message}"
            self._mark_connection_failure(
                attempted_at,
                message,
                f"人工只读抓取：{message}",
            )
            return
        except Exception:
            message = FAILURE_MESSAGES_ZH[TdxFailureReason.INVALID_RESPONSE]
            self.last_fetch_detail = f"人工只读抓取失败：{message}"
            self._mark_connection_failure(
                attempted_at,
                message,
                f"人工只读抓取：{message}",
            )
            return
        self.last_connection_check = attempted_at
        self.last_fetch_detail = (
            "人工只读抓取成功：官方全市场证券列表已返回并通过格式校验；"
            "列表正文未显示、未保存。"
        )
        self._mark_preflight_verified(checked_at=attempted_at)
        self.health_detail = (
            "TQ 已连接，人工只读抓取成功；数据门尚未通过，候选和提醒保持关闭。"
        )

    def _mark_preflight_verified(self, *, checked_at: datetime | None = None) -> None:
        self.last_connection_check = checked_at or self._clock()
        self.connection_state = TqConnectionState.CONNECTED
        self.connection_detail = (
            "本机端口和官方只读列表接口均已通过；应用每 60 秒自动重新检测。"
        )
        self.state = HealthState.WARMING
        self.data_gate_label = "未就绪"
        self.candidate_gate_label = "关闭"
        self.status_issues = _DATA_GATE_ISSUES
        self.health_detail = (
            "TQ 已连接；分钟历史、秒级源时间戳和真实交易时段 M0 尚未通过，"
            "因此候选和提醒保持关闭。"
        )

    def _mark_connection_failure(
        self,
        checked_at: datetime,
        detail: str,
        *issues: str,
    ) -> None:
        self.last_connection_check = checked_at
        self.connection_state = TqConnectionState.DISCONNECTED
        self.connection_detail = detail
        self.state = HealthState.STOPPED
        self.data_gate_label = "已阻断"
        self.candidate_gate_label = "关闭"
        self.status_issues = tuple(issue for issue in issues if issue) or (
            "TQ 连接：严格检测未通过。",
        )
        self.health_detail = detail
