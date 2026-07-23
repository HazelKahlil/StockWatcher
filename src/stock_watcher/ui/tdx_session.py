from __future__ import annotations

from pathlib import Path

from stock_watcher.domain import HealthState
from stock_watcher.engine.candidates import CandidateBatch
from stock_watcher.providers.tdxquant_preflight import CheckStatus, run_preflight
from stock_watcher.storage import SQLiteStore


class TdxDiagnosticSession:
    """Safe UI session before Windows M0 authorizes candidate production."""

    source_label = "官方通达信 TdxQuant（只读预检）"
    phase_label = "Windows 现场预检 · 候选尚未放行"
    app_badge = "Windows TQ 预检版"
    window_title = "A股观察提醒 · Windows TQ 预检版"
    is_replay = False

    def __init__(self, store_path: Path, endpoint: str) -> None:
        self.store = SQLiteStore(store_path)
        self.store.initialize()
        self.endpoint = endpoint
        self.batch: CandidateBatch | None = None
        self.state = HealthState.WARMING
        self.health_detail = ""
        self.recover()

    def stop(self) -> None:
        self.state = HealthState.STOPPED
        self.health_detail = "用户已暂停实时观察；恢复前不会产生新候选。"

    def warm_and_recover(self) -> None:
        self.state = HealthState.WARMING
        self.health_detail = "正在重新执行本机 TQ 预检。"

    def recover(self) -> None:
        report = run_preflight(endpoint=self.endpoint)
        if report.status is CheckStatus.FAIL:
            self.state = HealthState.STOPPED
            failure = next(
                (check.message for check in report.checks if check.status is CheckStatus.FAIL),
                "TQ 预检失败。",
            )
            self.health_detail = failure
            return
        self.state = HealthState.WARMING
        self.health_detail = (
            "TQ 本机预检通过；真实 Windows M0 与字段授权尚未完成，"
            "因此候选和提醒保持关闭。请运行“执行 M0 探针”。"
        )
