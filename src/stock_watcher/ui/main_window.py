from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from stock_watcher.domain import HealthState
from stock_watcher.storage import SQLiteStore

from .demo import demo_batch, demo_clock, recovery_clock
from .history import HistoryDialog
from .popup import AlertPopup
from .presenter import CandidateRow, UiSnapshot, format_change, format_time, snapshot_from_batch


class CandidateDetailDialog(QDialog):
    def __init__(self, row: CandidateRow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"候选详情 · {row.name} {row.code}")
        self.resize(560, 360)
        layout = QVBoxLayout(self)
        title = QLabel(f"{row.name}  {row.code}  ·  {row.level}")
        title.setObjectName("detailTitle")
        layout.addWidget(title)
        form = QFormLayout()
        form.addRow("当前价格", QLabel(f"¥{row.price:.2f}"))
        form.addRow("当前涨幅", QLabel(format_change(row.change_pct)))
        form.addRow("涨速", QLabel(format_change(row.velocity_pct)))
        form.addRow("板块", QLabel(row.sector))
        form.addRow("总分", QLabel(f"{row.score:.2f}"))
        form.addRow("数据时间", QLabel(format_time(row.source_ts)))
        form.addRow("Provider / 配置", QLabel(f"{row.provider_version} / {row.config_version}"))
        form.addRow("资金模块", QLabel("未就绪（M0 未通过；未使用替代字段）"))
        layout.addLayout(form)
        reason_box = QGroupBox("入选原因（可追溯）")
        reason_layout = QVBoxLayout(reason_box)
        for reason in row.reasons:
            reason_layout.addWidget(QLabel(f"• {reason}"))
        layout.addWidget(reason_box)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        layout.addWidget(close)


class ReplaySession:
    def __init__(self, store_path: Path) -> None:
        self.store = SQLiteStore(store_path)
        self.store.initialize()
        try:
            self.store.record_config_version("v0.2-mac-alpha", "synthetic-demo", '{"seed": 7}')
        except FileExistsError:
            # A repeated demo run may reuse an explicitly supplied database;
            # config versions are immutable, so retaining the existing row is safe.
            pass
        self.batch = demo_batch(demo_clock())
        self.snapshot_id = self.store.record_batch(self.batch)
        self.store.record_alert_event(
            self.snapshot_id, demo_clock().isoformat(), "changed", "desktop-demo"
        )
        self.state = HealthState.HEALTHY
        self.health_detail = "固定 Synthetic 场景；候选与界面字段来自同一回放批次"

    def stop(self) -> None:
        self.state = HealthState.STOPPED
        self.health_detail = "模拟数据中断；保留旧结果但停止产生新候选"

    def warm_and_recover(self) -> None:
        self.state = HealthState.WARMING
        self.health_detail = "恢复预热中：需新鲜样本后才恢复候选"

    def recover(self) -> None:
        self.state = HealthState.HEALTHY
        self.health_detail = "恢复完成；新鲜回放样本已通过健康门"
        self.batch = demo_batch(recovery_clock())
        self.snapshot_id = self.store.record_batch(self.batch)


class MainWindow(QMainWindow):
    def __init__(self, session: ReplaySession) -> None:
        super().__init__()
        self.session = session
        self._popup: AlertPopup | None = None
        self._rows: dict[str, CandidateRow] = {}
        self.setWindowTitle("StockWatcher · Mac Replay Alpha")
        self.resize(1040, 720)
        self._build()
        self._refresh()
        QTimer.singleShot(250, self._show_initial_alert)

    def _build(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        banner = QFrame()
        banner.setObjectName("demoBanner")
        banner_layout = QGridLayout(banner)
        banner_layout.addWidget(QLabel("模拟 / 回放数据"), 0, 0)
        banner_layout.addWidget(QLabel("Mac 本地 Alpha · 只做候选观察与异动提醒"), 0, 1)
        banner_layout.addWidget(QLabel("资金模块：未就绪"), 0, 2)
        root.addWidget(banner)

        title_row = QGridLayout()
        title = QLabel("当前观察")
        title.setObjectName("pageTitle")
        title_row.addWidget(title, 0, 0)
        self._health = QLabel()
        self._health.setObjectName("healthBadge")
        title_row.addWidget(self._health, 0, 1)
        title_row.setColumnStretch(0, 1)
        root.addLayout(title_row)

        status = QGroupBox("运行状态")
        status_layout = QGridLayout(status)
        self._source = QLabel()
        self._updated = QLabel()
        self._phase = QLabel()
        self._overall = QLabel()
        status_fields = (
            ("数据源", self._source),
            ("最后更新时间", self._updated),
            ("交易阶段", self._phase),
            ("本轮判断", self._overall),
        )
        for row, (label, widget) in enumerate(status_fields):
            status_layout.addWidget(QLabel(label), row, 0)
            status_layout.addWidget(widget, row, 1)
        status_layout.setColumnStretch(1, 1)
        root.addWidget(status)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["排序", "名称", "代码", "涨幅", "价格", "强度", "总分 / 板块"]
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.cellDoubleClicked.connect(self._open_detail_at)
        root.addWidget(self._table)

        self._top20_toggle = QToolButton()
        self._top20_toggle.setText("Top 20（默认折叠）")
        self._top20_toggle.setCheckable(True)
        self._top20_toggle.setChecked(False)
        self._top20_toggle.toggled.connect(self._toggle_top20)
        root.addWidget(self._top20_toggle)
        self._top20_hint = QLabel("后台候选已计算；当前只展示可见 Top3。")
        self._top20_hint.setVisible(False)
        root.addWidget(self._top20_hint)

        self._fund = QLabel()
        self._fund.setObjectName("muted")
        root.addWidget(self._fund)
        buttons = QGridLayout()
        disconnect = QPushButton("模拟数据中断")
        disconnect.clicked.connect(self._stop_replay)
        recover = QPushButton("恢复回放")
        recover.clicked.connect(self._recover_replay)
        detail = QPushButton("打开当前详情")
        detail.clicked.connect(self._open_first_detail)
        history = QPushButton("历史批次（只读）")
        history.clicked.connect(self._open_history)
        for col, button in enumerate((disconnect, recover, detail, history)):
            buttons.addWidget(button, 0, col)
        root.addLayout(buttons)
        self.setCentralWidget(central)

    def _snapshot(self) -> UiSnapshot:
        return snapshot_from_batch(
            self.session.batch,
            health=self.session.state,
            health_detail=self.session.health_detail,
            phase_label="盘中观察 · 回放时间 09:45",
        )

    def _refresh(self) -> None:
        snapshot = self._snapshot()
        self._health.setText(f"{snapshot.health.value} · {snapshot.health_detail}")
        self._health.setProperty("state", snapshot.health.value)
        self._health.style().unpolish(self._health)
        self._health.style().polish(self._health)
        self._source.setText(snapshot.source_label)
        self._updated.setText(format_time(snapshot.last_updated))
        self._phase.setText(snapshot.phase_label)
        self._overall.setText(snapshot.overall_label)
        self._fund.setText(snapshot.fund_label)
        self._rows = {row.code: row for row in snapshot.candidates}
        self._table.setRowCount(len(snapshot.candidates))
        for index, row in enumerate(snapshot.candidates):
            values = (
                str(index + 1),
                row.name,
                row.code,
                format_change(row.change_pct),
                f"¥{row.price:.2f}",
                row.level,
                f"{row.score:.2f} · {row.sector}",
            )
            for column, value in enumerate(values):
                self._table.setItem(index, column, QTableWidgetItem(value))

    def _show_initial_alert(self) -> None:
        snapshot = self._snapshot()
        if not snapshot.alert_allowed:
            return
        self._show_alert(snapshot)

    def _show_alert(self, snapshot: UiSnapshot) -> None:
        old_popup = self._popup
        self._popup = None
        if old_popup is not None:
            try:
                old_popup.close()
            except RuntimeError:
                # WA_DeleteOnClose can destroy the native object before the
                # Python callback that requested the next batch runs.
                pass
        title = f"异动观察 · {format_time(snapshot.last_updated)} · {snapshot.overall_label}"
        self._popup = AlertPopup(snapshot.candidates, title, self._open_detail_by_code)
        self._popup.show_at_bottom_right()
        self._popup.row_clicked.connect(lambda _code: None)

    def _stop_replay(self) -> None:
        self.session.stop()
        if self._popup is not None:
            self._popup.close()
            self._popup = None
        self._refresh()

    def _recover_replay(self) -> None:
        self.session.warm_and_recover()
        self._refresh()
        QTimer.singleShot(900, self._finish_recovery)

    def _finish_recovery(self) -> None:
        self.session.recover()
        self._refresh()
        snapshot = self._snapshot()
        if snapshot.alert_allowed:
            self._show_alert(snapshot)

    def _open_first_detail(self) -> None:
        if self._rows:
            self._open_detail_by_code(next(iter(self._rows)))

    def _open_detail_at(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._rows):
            code_item = self._table.item(row, 2)
            if code_item is not None:
                self._open_detail_by_code(code_item.text())

    def _open_detail_by_code(self, code: str) -> None:
        row = self._rows.get(code)
        if row is not None:
            CandidateDetailDialog(row, self).exec()

    def _open_history(self) -> None:
        dialog = HistoryDialog(self.session.store.path, self)
        dialog.exec()

    def _toggle_top20(self, visible: bool) -> None:
        self._top20_hint.setVisible(visible)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._popup is not None:
            self._popup.close()
        event.accept()
