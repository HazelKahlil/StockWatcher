from __future__ import annotations

from pathlib import Path
from typing import Protocol

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QMouseEvent
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from stock_watcher.domain import HealthState
from stock_watcher.engine.candidates import CandidateBatch
from stock_watcher.storage import SQLiteStore

from .demo import demo_batch, demo_clock, recovery_clock
from .history import HistoryDialog
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
        self.setMinimumHeight(96)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
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
        level.setObjectName("levelBadge")
        level.setProperty("level", row.level)
        level.setAlignment(Qt.AlignmentFlag.AlignCenter)
        level.setFixedWidth(58)
        layout.addWidget(level)

        sector = QVBoxLayout()
        sector.setSpacing(3)
        sector_label = QLabel("所属板块")
        sector_label.setObjectName("candidateMeta")
        sector_value = QLabel(row.sector)
        sector_value.setObjectName("candidateSector")
        sector.addWidget(sector_label)
        sector.addWidget(sector_value)
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
        level.setObjectName("levelBadge")
        level.setProperty("level", row.level)
        level.setAlignment(Qt.AlignmentFlag.AlignCenter)
        level.setFixedWidth(58)
        heading.addWidget(level)
        root.addLayout(heading)

        metrics = QFrame()
        metrics.setObjectName("metricsCard")
        metric_layout = QHBoxLayout(metrics)
        metric_layout.setContentsMargins(20, 18, 20, 18)
        for label, value, level_name in (
            ("当前价格", f"¥{row.price:.2f}", "neutral"),
            ("当前涨幅", format_change(row.change_pct), "up"),
            ("涨速", format_change(row.velocity_pct), "medium"),
            ("所属板块", row.sector, "neutral"),
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

        conclusion = QLabel("符合本轮观察条件，可自行打开通达信进一步确认。")
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

    def stop(self) -> None: ...

    def warm_and_recover(self) -> None: ...

    def recover(self) -> None: ...


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

    def warm_and_recover(self) -> None:
        self.state = HealthState.WARMING
        self.health_detail = "恢复预热中：需新鲜样本后才恢复候选"

    def recover(self) -> None:
        self.state = HealthState.HEALTHY
        self.health_detail = "恢复完成；新鲜回放样本已通过健康门"
        self.batch = demo_batch(recovery_clock())
        self.snapshot_id = self.store.record_batch(self.batch)


class MainWindow(QMainWindow):
    def __init__(self, session: UiSession) -> None:
        super().__init__()
        self.session = session
        self._popup: AlertPopup | None = None
        self._rows: dict[str, CandidateRow] = {}
        self._last_alert_signature: tuple[str, ...] | None = None
        self._actions_bound = False
        self.setWindowTitle(session.window_title)
        self.resize(1040, 720)
        self.setMinimumSize(860, 620)
        self._build()
        self._refresh()
        QTimer.singleShot(250, self._show_initial_alert)

    def _build(self) -> None:
        self._build_developer_menu()
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(34, 26, 34, 24)
        root.setSpacing(16)

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
        summary_layout = QHBoxLayout(self._summary_card)
        summary_layout.setContentsMargins(22, 18, 22, 18)
        self._updated = self._add_summary_item(summary_layout, "更新时间")
        self._overall = self._add_summary_item(summary_layout, "整体状态")
        self._result = self._add_summary_item(summary_layout, "本轮结果")
        root.addWidget(self._summary_card)

        self._interrupt_card = QFrame()
        self._interrupt_card.setObjectName("interruptCard")
        interrupt_layout = QVBoxLayout(self._interrupt_card)
        interrupt_layout.setContentsMargins(24, 20, 24, 20)
        self._interrupt_title = QLabel("数据中断")
        self._interrupt_title.setObjectName("interruptTitle")
        self._interrupt_message = QLabel()
        self._interrupt_message.setObjectName("interruptMessage")
        self._interrupt_last_update = QLabel()
        self._interrupt_last_update.setObjectName("interruptMeta")
        interrupt_layout.addWidget(self._interrupt_title)
        interrupt_layout.addWidget(self._interrupt_message)
        interrupt_layout.addWidget(self._interrupt_last_update)
        root.addWidget(self._interrupt_card)

        self._candidate_label = QLabel()
        self._candidate_label.setObjectName("sectionTitle")
        root.addWidget(self._candidate_label)
        self._cards = QVBoxLayout()
        self._cards.setSpacing(10)
        root.addLayout(self._cards, 1)

        actions = QHBoxLayout()
        self._primary_action = QPushButton()
        self._primary_action.setObjectName("primaryButton")
        self._secondary_action = QPushButton()
        self._secondary_action.setObjectName("secondaryButton")
        actions.addWidget(self._primary_action, 1)
        actions.addWidget(self._secondary_action, 1)
        root.addLayout(actions)

        footer = QHBoxLayout()
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_dot = QLabel("●")
        status_dot.setObjectName("statusDot")
        footer.addWidget(status_dot)
        self._footer = QLabel(self.session.app_badge)
        self._footer.setObjectName("footer")
        footer.addWidget(self._footer)
        root.addLayout(footer)
        self.setCentralWidget(central)

    def _add_summary_item(self, layout: QHBoxLayout, label: str) -> QLabel:
        cell = QVBoxLayout()
        cell.setSpacing(4)
        caption = QLabel(label)
        caption.setObjectName("summaryLabel")
        value = QLabel()
        value.setObjectName("summaryValue")
        cell.addWidget(caption)
        cell.addWidget(value)
        layout.addLayout(cell, 1)
        return value

    def _build_developer_menu(self) -> None:
        developer = self.menuBar().addMenu("开发")
        stop = QAction("模拟数据中断" if self.session.is_replay else "暂停实时观察", self)
        stop.triggered.connect(self._stop_replay)
        developer.addAction(stop)
        recover = QAction("恢复回放" if self.session.is_replay else "重新执行 TQ 预检", self)
        recover.triggered.connect(self._recover_replay)
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
        self._page_title.setText("当前观察" if healthy else "数据中断")
        self._summary_card.setVisible(healthy)
        self._interrupt_card.setVisible(not healthy)
        self._candidate_label.setText("本轮候选" if healthy else "上次结果，仅供参考")
        self._updated.setText(format_time(snapshot.last_updated))
        self._overall.setText(
            "偏弱" if snapshot.overall_label == "整体偏弱" else snapshot.overall_label
        )
        self._result.setText(f"{len(snapshot.candidates)}只可看")
        self._interrupt_message.setText(
            "数据暂时中断，已停止生成新候选。" if stopped else "正在恢复数据，请稍候。"
        )
        last_time = snapshot.last_updated.strftime("%H:%M") if snapshot.last_updated else "—"
        self._interrupt_last_update.setText(f"上次成功更新时间：{last_time}")

        rows = snapshot.candidates if healthy else snapshot.previous_candidates
        self._rows = {row.code: row for row in rows}
        self._clear_cards()
        for index, row in enumerate(rows[:3], start=1):
            card = CandidateCard(index, row, previous=not healthy)
            card.clicked.connect(self._open_detail_by_code)
            self._cards.addWidget(card)

        self._primary_action.setText("刷新" if healthy else "重新连接")
        self._secondary_action.setText("历史记录")
        if self._actions_bound:
            self._primary_action.clicked.disconnect()
        if healthy:
            self._primary_action.clicked.connect(self._refresh)
        else:
            self._primary_action.clicked.connect(self._recover_replay)

        if self._actions_bound:
            self._secondary_action.clicked.disconnect()
        self._secondary_action.clicked.connect(self._open_history)
        self._actions_bound = True

    def _show_initial_alert(self) -> None:
        snapshot = self._snapshot()
        if snapshot.alert_allowed:
            self._show_alert(snapshot)

    def _show_alert(self, snapshot: UiSnapshot) -> None:
        signature = tuple(row.code for row in snapshot.candidates)
        if signature == self._last_alert_signature and self._popup is not None:
            return
        if signature == self._last_alert_signature:
            return
        self._last_alert_signature = signature
        if self._popup is not None:
            self._popup.close()
        self._popup = AlertPopup(
            snapshot.candidates,
            (
                f"{format_time(snapshot.last_updated)} · "
                f"{('偏弱' if snapshot.overall_label == '整体偏弱' else snapshot.overall_label)}"
            ),
            self._open_detail_by_code,
        )
        self._popup.show_at_bottom_right()

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

    def _open_detail_by_code(self, code: str) -> None:
        row = self._rows.get(code)
        if row is not None:
            CandidateDetailDialog(row, self).exec()

    def _open_history(self) -> None:
        HistoryDialog(self.session.store.path, self).exec()

    def _open_developer_info(self) -> None:
        DeveloperInfoDialog(self.session, self).exec()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._popup is not None:
            self._popup.close()
        event.accept()
