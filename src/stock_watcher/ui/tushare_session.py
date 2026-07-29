from __future__ import annotations

from datetime import datetime
from pathlib import Path

from stock_watcher.config import DataSourceMode
from stock_watcher.domain import HealthState
from stock_watcher.engine.candidates import CandidateBatch
from stock_watcher.storage import SQLiteStore

from .tdx_session import TqConnectionState


class TushareDiagnosticSession:
    """Safe M0 shell. It never emits candidates before the live data gate passes."""

    source_label = "Tushare 兼容 HTTP（真实 M0 尚未完成）"
    phase_label = "Tushare Data Gate · 候选关闭"
    app_badge = "Windows Tushare 数据闸门"
    window_title = "A股观察提醒 · Tushare 数据闸门"
    is_replay = False
    supports_manual_fetch = False
    auto_check_interval_seconds = 60
    connection_name = "数据接口"
    reconnect_label = "检查数据接口"
    manual_fetch_label = "立即抓取（M0）"
    footer_label = "Tushare 兼容 HTTP · 凭据仅存系统安全存储 · 候选关闭"

    def __init__(self, store_path: Path) -> None:
        self.store = SQLiteStore(store_path)
        self.store.initialize()
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
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.DISCONNECTED
        self.connection_detail = "请从“设置 → 数据接口”执行凭据测试。"

    def begin_manual_fetch(self) -> None:
        return

    def manual_fetch(self) -> None:
        return
