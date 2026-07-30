from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from stock_watcher.config import DataSourceMode
from stock_watcher.domain import HealthState
from stock_watcher.engine.candidates import CandidateBatch
from stock_watcher.storage import SQLiteStore

from .connection_state import ConnectionState as TqConnectionState
from .daily_summary import DailySummaryDialog
from .data_source_settings import (
    DataSourceSettingsController,
    DataSourceSettingsDialog,
    runtime_data_source_controller,
)
from .demo import demo_batch, demo_clock, recovery_clock
from .history import HistoryDialog
from .macos import MacWindowClosePolicy
from .popup import AlertPopup
from .presenter import (
    CandidateRow,
    UiSnapshot,
    detail_reasons,
    format_change,
    format_time,
    snapshot_from_batch,
)


class CandidateCard(QFrame):
    clicked = Signal(str)

    def __init__(
        self,
        rank: int,
        row: CandidateRow,
        *,
        previous: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.code = row.code
        self.setObjectName("candidateCard")
        self.setProperty("level", row.level)
        self.setProperty("previous", previous)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(104)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(18)

        rank_label = QLabel(str(rank))
        rank_label.setObjectName("rankBadge")
        rank_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rank_label.setFixedSize(38, 38)
        layout.addWidget(rank_label)

        identity = QVBoxLayout()
        identity.setSpacing(3)
        name = QLabel(row.name)
        name.setObjectName("candidateName")
        code = QLabel(row.code)
        code.setObjectName("candidateCode")
        identity.addWidget(name)
        identity.addWidget(code)
        layout.addLayout(identity, 1)

        quote = QVBoxLayout()
        quote.setSpacing(3)
        change = QLabel(format_change(row.change_pct))
        change.setObjectName("candidateChange")
        price = QLabel(f"¥{row.price:.2f}")
        price.setObjectName("candidatePrice")
        quote.addWidget(change)
        quote.addWidget(price)
        layout.addLayout(quote)

        level = QLabel(row.level)
        if row.is_supplement:
            level.setText("近｜补位观察")
        level.setObjectName("levelBadge")
        level.setProperty("level", row.level)
        level.setAlignment(Qt.AlignmentFlag.AlignCenter)
        level.setFixedWidth(112 if row.is_supplement else 58)
        layout.addWidget(level)

        sector = QVBoxLayout()
        sector.setSpacing(3)
        sector_label = QLabel("最强板块")
        sector_label.setObjectName("candidateMeta")
        sector_value = QLabel(row.sector)
        sector_value.setObjectName("candidateSector")
        sector.addWidget(sector_label)
        sector.addWidget(sector_value)
        fund = QLabel(row.fund_label)
        fund.setObjectName("candidateMeta")
        sector.addWidget(fund)
        layout.addLayout(sector, 1)

        arrow = QLabel("›")
        arrow.setObjectName("cardArrow")
        layout.addWidget(arrow)
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if previous:
            opacity = QGraphicsOpacityEffect(self)
            opacity.setOpacity(0.62)
            self.setGraphicsEffect(opacity)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.code)
        super().mousePressEvent(event)


class CandidateDetailDialog(QDialog):
    def __init__(self, row: CandidateRow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{row.name} {row.code}")
        self.resize(760, 500)
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 30, 24)
        root.setSpacing(20)

        heading = QHBoxLayout()
        title = QLabel(f"{row.name}  {row.code}")
        title.setObjectName("dialogTitle")
        heading.addWidget(title)
        heading.addStretch()
        level = QLabel(row.level)
        if row.is_supplement:
            level.setText("近｜补位观察")
        level.setObjectName("levelBadge")
        level.setProperty("level", row.level)
        level.setAlignment(Qt.AlignmentFlag.AlignCenter)
        level.setFixedWidth(112 if row.is_supplement else 58)
        heading.addWidget(level)
        root.addLayout(heading)

        metrics = QFrame()
        metrics.setObjectName("metricsCard")
        metric_layout = QHBoxLayout(metrics)
        metric_layout.setContentsMargins(20, 18, 20, 18)
        for label, value, level_name in (
            ("当前价格", f"¥{row.price:.2f}", "neutral"),
            ("当前涨幅", format_change(row.change_pct), "up"),
            ("1分钟涨速", format_change(row.velocity_pct), "medium"),
            ("最强板块", row.sector, "neutral"),
        ):
            cell = QVBoxLayout()
            caption = QLabel(label)
            caption.setObjectName("metricLabel")
            value_label = QLabel(value)
            value_label.setObjectName("metricValue")
            value_label.setProperty("tone", level_name)
            cell.addWidget(caption)
            cell.addWidget(value_label)
            metric_layout.addLayout(cell, 1)
        root.addWidget(metrics)

        reasons_title = QLabel("为什么进入本轮观察")
        reasons_title.setObjectName("sectionTitle")
        root.addWidget(reasons_title)
        reasons = QFrame()
        reasons.setObjectName("reasonCard")
        reason_layout = QVBoxLayout(reasons)
        reason_layout.setContentsMargins(20, 16, 20, 16)
        reason_layout.setSpacing(12)
        for title_text, explanation in detail_reasons(row):
            line = QHBoxLayout()
            reason = QLabel(title_text)
            reason.setObjectName("reasonTitle")
            explanation_label = QLabel(explanation)
            explanation_label.setObjectName("reasonText")
            explanation_label.setWordWrap(True)
            line.addWidget(reason)
            line.addWidget(explanation_label, 1)
            reason_layout.addLayout(line)
        root.addWidget(reasons)

        status = QFrame()
        status.setObjectName("reasonCard")
        status_layout = QFormLayout(status)
        status_layout.addRow("资金情况", QLabel(row.fund_label))
        status_layout.addRow("三日趋势", QLabel(row.trend_label))
        root.addWidget(status)

        conclusion = QLabel(
            "本轮属于补位观察，核心门槛尚未全部通过。"
            if row.is_supplement
            else "进入当前观察，可自行打开行情软件进一步确认。"
        )
        conclusion.setObjectName("conclusion")
        conclusion.setWordWrap(True)
        root.addWidget(conclusion)
        root.addStretch()

        close = QPushButton("返回列表")
        close.setObjectName("primaryButton")
        close.clicked.connect(self.accept)
        root.addWidget(close)


class UiSession(Protocol):
    store: SQLiteStore
    batch: CandidateBatch | None
    state: HealthState
    health_detail: str
    source_label: str
    phase_label: str
    app_badge: str
    window_title: str
    is_replay: bool
    supports_manual_fetch: bool
    auto_check_interval_seconds: int
    connection_state: TqConnectionState
    connection_detail: str
    data_gate_label: str
    candidate_gate_label: str
    last_connection_check: datetime | None
    last_fetch_at: datetime | None
    last_fetch_detail: str
    status_issues: tuple[str, ...]
    connection_name: str
    reconnect_label: str
    manual_fetch_label: str
    footer_label: str

    def stop(self) -> None: ...

    def warm_and_recover(self) -> None: ...

    def recover(self) -> None: ...

    def begin_manual_fetch(self) -> None: ...

    def manual_fetch(self) -> None: ...

    def provider_changed(self, mode: DataSourceMode) -> None: ...


class _SessionOperationWorker(QObject):
    finished = Signal()

    def __init__(self, session: UiSession, operation: str) -> None:
        super().__init__()
        self._session = session
        self._operation = operation

    @Slot()
    def run(self) -> None:
        try:
            if self._operation == "fetch":
                self._session.manual_fetch()
            else:
                self._session.recover()
        finally:
            self.finished.emit()


class DeveloperInfoDialog(QDialog):
    def __init__(self, session: UiSession, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("开发信息")
        self.resize(520, 300)
        root = QVBoxLayout(self)
        title = QLabel("开发信息（普通用户不可见）")
        title.setObjectName("sectionTitle")
        root.addWidget(title)
        form = QFormLayout()
        first = session.batch.candidates[0] if session.batch and session.batch.candidates else None
        fields = (
            ("状态", session.state.value),
            (f"{session.connection_name}连接", session.connection_state.value),
            ("连接说明", session.connection_detail),
            ("数据门", session.data_gate_label),
            ("候选", session.candidate_gate_label),
            ("数据场景", session.source_label),
            ("Provider", first.provider_version if first else "—"),
            ("配置版本", first.config_version if first else "—"),
            ("资金模块", "unavailable（M0 未就绪）"),
            ("诊断", session.health_detail),
        )
        for label, value in fields:
            form.addRow(label, QLabel(value))
        root.addLayout(form)
        root.addStretch()
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        root.addWidget(close)


class ReplaySession:
    source_label = "Mock / Replay（模拟/回放数据）"
    phase_label = "盘中观察 · 回放时间 09:45"
    app_badge = "Mac 测试版"
    window_title = "A股观察提醒 · Mac 测试版"
    is_replay = True
    supports_manual_fetch = False
    auto_check_interval_seconds = 0
    connection_state = TqConnectionState.NOT_APPLICABLE
    connection_detail = "回放模式不连接 TQ。"
    data_gate_label = "正常"
    candidate_gate_label = "已开放"
    last_connection_check: datetime | None = None
    last_fetch_at: datetime | None = None
    last_fetch_detail = "回放模式不执行人工抓取。"
    status_issues: tuple[str, ...] = ()
    connection_name = "回放"
    reconnect_label = "恢复回放"
    manual_fetch_label = "立即抓取（只读）"
    footer_label = "Mock / Replay · 不连接真实数据"
    advanced_diagnostics = True

    def __init__(self, store_path: Path) -> None:
        self.store = SQLiteStore(store_path)
        self.store.initialize()
        try:
            self.store.record_config_version("v0.2-mac-alpha", "synthetic-demo", '{"seed": 7}')
        except FileExistsError:
            # A repeated demo run may reuse an explicitly supplied database;
            # config versions are immutable, so retaining the existing row is safe.
            pass
        batch = demo_batch(demo_clock())
        self.batch: CandidateBatch | None = batch
        historical_batch = demo_batch(demo_clock().replace(minute=15))
        self.store.record_batch(historical_batch)
        self.snapshot_id = self.store.record_batch(batch)
        self.store.record_alert_event(
            self.snapshot_id, demo_clock().isoformat(), "changed", "desktop-demo"
        )
        self.state = HealthState.HEALTHY
        self.health_detail = "固定 Synthetic 场景；候选与界面字段来自同一回放批次"

    def stop(self) -> None:
        self.state = HealthState.STOPPED
        self.health_detail = "数据源中断；保留旧结果但停止产生新候选"
        self.data_gate_label = "已中断"
        self.candidate_gate_label = "关闭"
        self.status_issues = ("回放数据源：已模拟中断。",)

    def warm_and_recover(self) -> None:
        self.state = HealthState.WARMING
        self.health_detail = "恢复预热中：需新鲜样本后才恢复候选"
        self.data_gate_label = "预热中"
        self.candidate_gate_label = "关闭"
        self.status_issues = ("回放数据源：正在恢复预热。",)

    def recover(self) -> None:
        self.state = HealthState.HEALTHY
        self.health_detail = "恢复完成；新鲜回放样本已通过健康门"
        self.data_gate_label = "正常"
        self.candidate_gate_label = "已开放"
        self.status_issues = ()
        self.batch = demo_batch(recovery_clock())
        self.snapshot_id = self.store.record_batch(self.batch)

    def begin_manual_fetch(self) -> None:
        return

    def manual_fetch(self) -> None:
        return

    def provider_changed(self, mode: DataSourceMode) -> None:
        return


class MainWindow(QMainWindow):
    def __init__(self, session: UiSession) -> None:
        super().__init__()
        self.session = session
        self._popup: AlertPopup | None = None
        self._rows: dict[str, CandidateRow] = {}
        self._last_alert_signature: tuple[str, ...] | None = None
        self._operation_thread: QThread | None = None
        self._operation_worker: _SessionOperationWorker | None = None
        self._active_operation: str | None = None
        self._mac_window_close_policy = MacWindowClosePolicy()
        self._secondary_notification: Callable[[str, str], bool] | None = None
        self._initial_data_source_dialog: DataSourceSettingsDialog | None = None
        self.setWindowTitle(session.window_title)
        self.resize(1040, 760)
        self.setMinimumSize(880, 640)
        self._build()
        self._refresh()
        self._auto_check_timer = QTimer(self)
        if not self.session.is_replay:
            interval_ms = max(5, self.session.auto_check_interval_seconds) * 1000
            self._auto_check_timer.setInterval(interval_ms)
            self._auto_check_timer.timeout.connect(self._auto_check_tq)
            self._auto_check_timer.start()
            QTimer.singleShot(0, self._auto_check_tq)
            if bool(getattr(self.session, "requires_data_source_setup", False)):
                # Do not block the event loop at startup.  On a first macOS
                # launch this still brings the simple Token page forward, but
                # leaves the main window responsive and testable.
                QTimer.singleShot(0, self._open_initial_data_source_settings)
        QTimer.singleShot(250, self._show_initial_alert)

    def _build(self) -> None:
        self._build_developer_menu()
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(28, 22, 28, 18)
        root.setSpacing(10)

        app_bar = QHBoxLayout()
        brand = QLabel("A股观察提醒")
        brand.setObjectName("appBrand")
        app_bar.addWidget(brand)
        test_badge = QLabel(self.session.app_badge)
        test_badge.setObjectName("testBadge")
        app_bar.addWidget(test_badge)
        app_bar.addStretch()
        root.addLayout(app_bar)

        title_row = QHBoxLayout()
        self._page_title = QLabel()
        self._page_title.setObjectName("pageTitle")
        title_row.addWidget(self._page_title)
        title_row.addStretch()
        root.addLayout(title_row)

        self._summary_card = QFrame()
        self._summary_card.setObjectName("summaryCard")
        self._summary_card.setMaximumHeight(88)
        summary_layout = QGridLayout(self._summary_card)
        summary_layout.setContentsMargins(18, 12, 18, 12)
        summary_layout.setHorizontalSpacing(20)
        self._health = self._add_summary_item(summary_layout, "数据状态", 0, 0)
        self._updated = self._add_summary_item(summary_layout, "最后更新时间", 0, 1)
        self._connection = self._add_summary_item(
            summary_layout, f"{self.session.connection_name}连接 / 最近检测", 0, 2
        )
        self._candidate_gate = self._add_summary_item(summary_layout, "候选状态", 0, 3)
        self._phase = self._add_summary_item(summary_layout, "当前阶段", 0, 4)
        root.addWidget(self._summary_card)

        self._interrupt_card = QFrame()
        self._interrupt_card.setObjectName("interruptCard")
        self._interrupt_card.setMaximumHeight(138)
        interrupt_layout = QVBoxLayout(self._interrupt_card)
        interrupt_layout.setContentsMargins(18, 12, 18, 12)
        interrupt_layout.setSpacing(5)
        interrupt_head = QHBoxLayout()
        interrupt_head.setSpacing(14)
        self._interrupt_title = QLabel("数据中断")
        self._interrupt_title.setObjectName("interruptTitle")
        self._interrupt_message = QLabel()
        self._interrupt_message.setObjectName("interruptMessage")
        self._interrupt_message.setWordWrap(True)
        interrupt_head.addWidget(self._interrupt_title)
        interrupt_head.addWidget(self._interrupt_message, 1)
        self._issue_list = QLabel()
        self._issue_list.setObjectName("issueList")
        self._issue_list.setWordWrap(True)
        self._interrupt_last_update = QLabel()
        self._interrupt_last_update.setObjectName("interruptMeta")
        self._interrupt_last_update.setWordWrap(True)
        interrupt_layout.addLayout(interrupt_head)
        interrupt_layout.addWidget(self._issue_list)
        interrupt_layout.addWidget(self._interrupt_last_update)
        root.addWidget(self._interrupt_card)

        self._candidate_label = QLabel()
        self._candidate_label.setObjectName("sectionTitle")
        root.addWidget(self._candidate_label)
        cards_host = QWidget()
        cards_host.setObjectName("cardsHost")
        self._cards = QVBoxLayout(cards_host)
        self._cards.setContentsMargins(0, 0, 0, 0)
        self._cards.setSpacing(10)
        self._cards_scroll = QScrollArea()
        self._cards_scroll.setObjectName("cardsScroll")
        self._cards_scroll.setWidgetResizable(True)
        self._cards_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._cards_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._cards_scroll.setWidget(cards_host)
        self._cards_scroll.setMinimumHeight(280)
        root.addWidget(self._cards_scroll, 1)

        actions = QHBoxLayout()
        self._primary_action = QPushButton()
        self._primary_action.setObjectName(
            "secondaryButton" if self.session.supports_manual_fetch else "primaryButton"
        )
        self._manual_fetch_action = QPushButton("立即抓取（只读）")
        self._manual_fetch_action.setObjectName(
            "primaryButton" if self.session.supports_manual_fetch else "secondaryButton"
        )
        self._secondary_action = QPushButton()
        self._secondary_action.setObjectName("secondaryButton")
        if self.session.supports_manual_fetch:
            actions.addWidget(self._manual_fetch_action, 2)
            actions.addWidget(self._primary_action, 1)
        else:
            actions.addWidget(self._primary_action, 1)
            actions.addWidget(self._manual_fetch_action, 1)
        actions.addWidget(self._secondary_action, 1)
        root.addLayout(actions)
        self._primary_action.clicked.connect(self._primary_clicked)
        self._manual_fetch_action.clicked.connect(self._manual_fetch_tq)
        self._secondary_action.clicked.connect(self._open_history)
        if not self.session.is_replay:
            self._primary_action.setToolTip(
                "重新检查当前数据接口，并用新鲜数据恢复观察名单。"
            )
            self._manual_fetch_action.setToolTip(
                "立即抓取一次最新全市场行情，更新主界面并弹出3只观察股票。"
            )

        footer = QHBoxLayout()
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_dot = QLabel("●")
        self._status_dot.setObjectName("statusDot")
        footer.addWidget(self._status_dot)
        self._footer = QLabel(self.session.app_badge)
        self._footer.setObjectName("footer")
        footer.addWidget(self._footer)
        root.addLayout(footer)
        self.setCentralWidget(central)

    def _add_summary_item(
        self,
        layout: QGridLayout,
        label: str,
        row: int,
        column: int,
    ) -> QLabel:
        cell = QVBoxLayout()
        cell.setSpacing(4)
        caption = QLabel(label)
        caption.setObjectName("summaryLabel")
        value = QLabel()
        value.setObjectName("summaryValue")
        value.setWordWrap(True)
        cell.addWidget(caption)
        cell.addWidget(value)
        layout.addLayout(cell, row, column)
        layout.setColumnStretch(column, 1)
        return value

    def _build_developer_menu(self) -> None:
        settings = self.menuBar().addMenu("设置")
        data_source = QAction("数据接口", self)
        data_source.triggered.connect(self._open_data_source_settings)
        settings.addAction(data_source)
        summary = QAction("收盘总结", self)
        summary.triggered.connect(self._open_daily_summary)
        settings.addAction(summary)

        if not getattr(self.session, "advanced_diagnostics", True):
            return
        developer = self.menuBar().addMenu("开发")
        stop = QAction("模拟数据中断" if self.session.is_replay else "暂停实时观察", self)
        stop.triggered.connect(self._stop_replay)
        developer.addAction(stop)
        recover = QAction(self.session.reconnect_label, self)
        recover.triggered.connect(
            self._recover_replay if self.session.is_replay else self._manual_check_tq
        )
        developer.addAction(recover)
        developer.addSeparator()
        info = QAction("开发信息", self)
        info.triggered.connect(self._open_developer_info)
        developer.addAction(info)

    def _snapshot(self) -> UiSnapshot:
        return snapshot_from_batch(
            self.session.batch,
            health=self.session.state,
            health_detail=self.session.health_detail,
            source_label=self.session.source_label,
            phase_label=self.session.phase_label,
        )

    def _clear_cards(self) -> None:
        while self._cards.count():
            item = self._cards.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()

    def _refresh(self) -> None:
        snapshot = self._snapshot()
        healthy = snapshot.health is HealthState.HEALTHY
        stopped = snapshot.health is HealthState.STOPPED
        connection = self.session.connection_state
        if self.session.is_replay:
            self._page_title.setText("当前观察" if healthy else "数据中断")
        elif connection is TqConnectionState.CONNECTED and healthy:
            self._page_title.setText("当前观察")
        elif connection is TqConnectionState.CHECKING:
            self._page_title.setText(f"正在检测{self.session.connection_name}")
        else:
            self._page_title.setText(f"{self.session.connection_name}未连接")

        health_labels = {
            HealthState.HEALTHY: "正常",
            HealthState.WARMING: "正在准备",
            HealthState.STALE: "数据已过期",
            HealthState.STOPPED: "已停止",
        }
        self._health.setText(health_labels[snapshot.health])
        self._updated.setText(
            format_time(snapshot.last_updated)
            if snapshot.last_updated is not None
            else "尚无合规候选数据"
        )
        connection_time = self._format_status_time(self.session.last_connection_check)
        self._connection.setText(
            connection.value
            if self.session.is_replay
            else f"{connection.value}\n{connection_time}"
        )
        self._connection.setProperty("state", connection.name.lower())
        self._repolish(self._connection)
        self._candidate_gate.setText(self.session.candidate_gate_label)
        self._phase.setText(self.session.phase_label)

        self._summary_card.setVisible(True)
        self._interrupt_card.setVisible(not healthy)
        if self.session.is_replay:
            self._candidate_label.setText("本轮候选" if healthy else "上次结果，仅供参考")
            self._interrupt_title.setText("数据中断" if stopped else "数据恢复中")
            self._interrupt_message.setText(
                "数据暂时中断，已停止生成新候选。"
                if stopped
                else "正在恢复数据，请稍候。"
            )
        else:
            self._candidate_label.setText(
                (
                    f"当前3只观察｜"
                    f"{'本轮整体偏弱' if snapshot.overall_label == '本轮整体偏弱' else '运行正常'}"
                )
                if healthy
                else "上次结果，仅供参考"
            )
            if connection is TqConnectionState.CONNECTED:
                self._interrupt_title.setText("连接正常，正在准备新结果")
            elif connection is TqConnectionState.CHECKING:
                self._interrupt_title.setText("正在检测连接")
            else:
                self._interrupt_title.setText("连接检查未通过")
            self._interrupt_message.setText(self.session.connection_detail)

        issues = self.session.status_issues
        self._issue_list.setText(
            " · ".join(issues[:2])
            if issues
            else ""
        )
        last_fetch = self._format_status_time(self.session.last_fetch_at)
        fetch_detail = self.session.last_fetch_detail.replace("\n", " ")
        self._interrupt_last_update.setText(
            f"最近连接检测：{self._format_status_time(self.session.last_connection_check)}"
            f"｜最近抓取：{last_fetch}｜{fetch_detail}"
        )

        rows = snapshot.candidates if healthy else snapshot.previous_candidates
        self._rows = {row.code: row for row in rows}
        self._clear_cards()
        for index, row in enumerate(rows[:3], start=1):
            card = CandidateCard(index, row, previous=not healthy)
            card.clicked.connect(self._open_detail_by_code)
            self._cards.addWidget(card)
        if not rows:
            empty = QLabel(
                "完成数据准备后，这里会固定显示3只观察股票。"
            )
            empty.setObjectName("emptyState")
            empty.setMinimumHeight(150)
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._cards.addWidget(empty)

        if self.session.is_replay:
            self._primary_action.setText("刷新" if healthy else "恢复回放")
        else:
            self._primary_action.setText(
                "连接中…" if self._active_operation == "check" else self.session.reconnect_label
            )
        self._manual_fetch_action.setText(
            "抓取中…" if self._active_operation == "fetch" else self.session.manual_fetch_label
        )
        self._manual_fetch_action.setVisible(self.session.supports_manual_fetch)
        busy = self._active_operation is not None
        self._primary_action.setEnabled(not busy)
        self._manual_fetch_action.setEnabled(not busy)
        self._secondary_action.setText("历史记录")

        if healthy:
            dot_state = "healthy"
        elif connection is TqConnectionState.DISCONNECTED:
            dot_state = "stopped"
        elif connection is TqConnectionState.CHECKING:
            dot_state = "checking"
        else:
            dot_state = "warming"
        self._status_dot.setProperty("state", dot_state)
        self._repolish(self._status_dot)
        self._footer.setText(self.session.footer_label)

    @staticmethod
    def _format_status_time(value: datetime | None) -> str:
        if value is None:
            return "尚未检测"
        return value.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

    def _show_initial_alert(self) -> None:
        snapshot = self._snapshot()
        if snapshot.alert_allowed:
            self._show_alert(snapshot, title="09:45 观察提醒")

    def _show_alert(
        self,
        snapshot: UiSnapshot,
        *,
        title: str,
        subtitle: str | None = None,
        force: bool = False,
    ) -> None:
        signature = tuple(row.code for row in snapshot.candidates)
        if not force and signature == self._last_alert_signature and self._popup is not None:
            return
        if not force and signature == self._last_alert_signature:
            return
        self._last_alert_signature = signature
        if self._popup is not None:
            self._popup.close()
        overall = (
            "偏弱"
            if snapshot.overall_label == "本轮整体偏弱"
            else snapshot.overall_label
        )
        alert_subtitle = subtitle or f"{format_time(snapshot.last_updated)} · {overall}"
        self._popup = AlertPopup(
            snapshot.candidates,
            title,
            alert_subtitle,
            self._open_detail_by_code,
            parent=self,
        )
        QApplication.beep()
        self._popup.show_at_bottom_right(preferred_screen=self.screen())
        if self._secondary_notification is not None:
            self._secondary_notification(title, alert_subtitle)

    def enable_background_close(self) -> None:
        """Keep scanning after the user closes the macOS main window."""
        self._mac_window_close_policy.enable_background_close()

    def request_application_exit(self) -> None:
        """Allow the explicit application-menu Quit action to close resources."""
        self._mac_window_close_policy.request_application_exit()

    def restore_main_window(self) -> None:
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def set_secondary_notification_sender(
        self,
        sender: Callable[[str, str], bool] | None,
    ) -> None:
        self._secondary_notification = sender

    def begin_platform_recovery(self, reason: str) -> None:
        handler = getattr(self.session, "begin_platform_recovery", None)
        if callable(handler):
            handler(reason)
        else:
            self.session.warm_and_recover()
        self._refresh()
        if not self.session.is_replay and not self._auto_check_timer.isActive():
            self._auto_check_timer.start()
        if not self.session.is_replay and self._active_operation is None:
            QTimer.singleShot(0, self._auto_check_tq)

    def mark_network_interrupted(self, reason: str) -> None:
        handler = getattr(self.session, "mark_network_interrupted", None)
        if callable(handler):
            handler(reason)
        else:
            self.session.stop()
        self._auto_check_timer.stop()
        self._refresh()

    def _stop_replay(self) -> None:
        self.session.stop()
        if self._popup is not None:
            self._popup.close()
            self._popup = None
        self._refresh()

    def _primary_clicked(self) -> None:
        if self.session.is_replay:
            if self.session.state is HealthState.HEALTHY:
                self._refresh()
            else:
                self._recover_replay()
            return
        self._manual_check_tq()

    def _recover_replay(self) -> None:
        self.session.warm_and_recover()
        self._refresh()
        QTimer.singleShot(900, self._finish_recovery)

    def _finish_recovery(self) -> None:
        self.session.recover()
        self._refresh()
        snapshot = self._snapshot()
        if snapshot.alert_allowed:
            self._show_alert(snapshot, title="本轮观察提醒")

    def _manual_check_tq(self) -> None:
        if self.session.is_replay:
            return
        self.session.warm_and_recover()
        self._start_tq_operation("check")

    def _manual_fetch_tq(self) -> None:
        if self.session.is_replay or not self.session.supports_manual_fetch:
            return
        self.session.begin_manual_fetch()
        self._start_tq_operation("fetch")

    def _auto_check_tq(self) -> None:
        if self.session.is_replay:
            return
        self._start_tq_operation("check")

    def _start_tq_operation(self, operation: str) -> None:
        if self._active_operation is not None or (
            self._operation_thread is not None and self._operation_thread.isRunning()
        ):
            return
        self._active_operation = operation
        self._refresh()
        thread = QThread(self)
        worker = _SessionOperationWorker(self.session, operation)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_tq_operation_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_tq_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._operation_thread = thread
        self._operation_worker = worker
        thread.start()

    @Slot()
    def _on_tq_operation_finished(self) -> None:
        self._active_operation = None
        self._refresh()
        consume = getattr(self.session, "consume_pending_alert", None)
        pending = consume() if callable(consume) else None
        snapshot = self._snapshot()
        if pending is not None:
            display_snapshot = snapshot
            is_fixed = str(pending.trigger_type).startswith("scheduled-")
            if (
                not snapshot.alert_allowed
                and is_fixed
                and len(snapshot.previous_candidates) == 3
            ):
                display_snapshot = replace(
                    snapshot,
                    candidates=snapshot.previous_candidates,
                    alert_allowed=True,
                )
            if not display_snapshot.alert_allowed:
                return
            self._show_alert(
                display_snapshot,
                title=str(pending.title),
                subtitle=str(pending.subtitle),
                force=True,
            )

    @Slot()
    def _on_tq_thread_finished(self) -> None:
        self._operation_thread = None
        self._operation_worker = None

    def _open_detail_by_code(self, code: str) -> None:
        row = self._rows.get(code)
        if row is not None:
            CandidateDetailDialog(row, self).exec()

    def _open_history(self) -> None:
        HistoryDialog(self.session.store.path, self).exec()

    def _open_daily_summary(self) -> None:
        DailySummaryDialog(self.session.store.path, self).exec()

    def _open_developer_info(self) -> None:
        DeveloperInfoDialog(self.session, self).exec()

    def _open_data_source_settings(self) -> None:
        DataSourceSettingsDialog(self._data_source_controller(), parent=self).exec()

    def _open_initial_data_source_settings(self) -> None:
        """Show the first-run Token page without entering a nested event loop."""
        dialog = self._initial_data_source_dialog
        if dialog is None:
            dialog = DataSourceSettingsDialog(self._data_source_controller(), parent=self)
            dialog.finished.connect(self._clear_initial_data_source_dialog)
            self._initial_data_source_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _clear_initial_data_source_dialog(self, _result: int) -> None:
        self._initial_data_source_dialog = None

    def _data_source_controller(self) -> DataSourceSettingsController:
        controller_factory = getattr(self.session, "data_source_controller", None)
        return (
            controller_factory()
            if callable(controller_factory)
            else runtime_data_source_controller(self.session.provider_changed)
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._mac_window_close_policy.should_hide_on_close:
            event.ignore()
            self.hide()
            return
        self._auto_check_timer.stop()
        if self._operation_thread is not None and self._operation_thread.isRunning():
            self._operation_thread.quit()
            self._operation_thread.wait(6000)
        if self._popup is not None:
            self._popup.close()
        if self._initial_data_source_dialog is not None:
            self._initial_data_source_dialog.close()
            self._initial_data_source_dialog = None
        shutdown = getattr(self.session, "shutdown", None)
        if callable(shutdown):
            shutdown()
        event.accept()
