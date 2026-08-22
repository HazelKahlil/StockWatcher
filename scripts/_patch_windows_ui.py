from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    content = target.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old[:100]!r}")
    target.write_text(content.replace(old, new, 1), encoding="utf-8")


# Platform font and Windows contrast.
replace_once(
    "src/stock_watcher/ui/app.py",
    "from PySide6.QtGui import QIcon\n",
    "from PySide6.QtGui import QFontDatabase, QIcon\n",
)
replace_once(
    "src/stock_watcher/ui/app.py",
    "QWidget { font-family: -apple-system, BlinkMacSystemFont, sans-serif; color: #172231; }\n",
    "QWidget { color: #172231; }\n",
)
replace_once(
    "src/stock_watcher/ui/app.py",
    "#secondaryButton:disabled { color: #a2acb9; background: #f5f7fa; }\n",
    "#secondaryButton:disabled { color: #738196; background: #eef2f7; border-color: #d7dee8; }\n",
)
replace_once(
    "src/stock_watcher/ui/app.py",
    "def application_icon_path() -> Path:\n",
    '''def application_font_candidates(platform: str = sys.platform) -> tuple[str, ...]:
    """Return platform-native UI font preferences without forcing a missing family."""

    if platform == "win32":
        return ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI")
    if platform == "darwin":
        return ("PingFang SC", "Helvetica Neue")
    return ("Noto Sans CJK SC", "Noto Sans", "DejaVu Sans")


def configure_application_font(
    app: QApplication,
    *,
    platform: str = sys.platform,
) -> str:
    """Select an installed platform font so Chinese copy never falls back to serif."""

    installed = {family.casefold(): family for family in QFontDatabase.families()}
    font = app.font()
    for candidate in application_font_candidates(platform):
        selected = installed.get(candidate.casefold())
        if selected is not None:
            font.setFamily(selected)
            break
    if font.pointSizeF() < 10.0:
        font.setPointSizeF(10.0)
    app.setFont(font)
    return font.family()


def application_icon_path() -> Path:
''',
)
replace_once(
    "src/stock_watcher/ui/app.py",
    '''        app.setApplicationName("StockWatcher")
        app.setOrganizationName("StockWatcher")
        icon_path = application_icon_path()
''',
    '''        app.setApplicationName("StockWatcher")
        app.setOrganizationName("StockWatcher")
        configure_application_font(app)
        icon_path = application_icon_path()
''',
)

# Main window responsive layout and lifecycle.
replace_once(
    "src/stock_watcher/ui/main_window.py",
    "from __future__ import annotations\n\nfrom collections.abc import Callable\n",
    "from __future__ import annotations\n\nimport sys\nfrom collections.abc import Callable\n",
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    "from PySide6.QtGui import QAction, QCloseEvent, QMouseEvent\n",
    "from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QMouseEvent\n",
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    "    QPushButton,\n    QScrollArea,\n    QVBoxLayout,\n",
    "    QPushButton,\n    QScrollArea,\n    QSizePolicy,\n    QVBoxLayout,\n",
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    '''        self._initial_data_source_dialog: DataSourceSettingsDialog | None = None
        self._closing = False
        self.setWindowTitle(session.window_title)
        self.resize(1040, 760)
        self.setMinimumSize(880, 640)
''',
    '''        self._initial_data_source_dialog: DataSourceSettingsDialog | None = None
        self._closing = False
        self._shutdown_requested = False
        self._shutdown_complete = False
        self._close_after_worker = False
        self.setWindowTitle(session.window_title)
        self.resize(1000, 720)
        self.setMinimumSize(760, 560)
''',
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    '''        self._summary_card = QFrame()
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
''',
    '''        self._summary_card = QFrame()
        self._summary_card.setObjectName("summaryCard")
        self._summary_card.setMinimumHeight(136)
        self._summary_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        summary_layout = QGridLayout(self._summary_card)
        summary_layout.setContentsMargins(18, 12, 18, 12)
        summary_layout.setHorizontalSpacing(20)
        summary_layout.setVerticalSpacing(10)
        self._health = self._add_summary_item(summary_layout, "数据状态", 0, 0)
        self._updated = self._add_summary_item(summary_layout, "最后更新时间", 0, 1)
        self._connection = self._add_summary_item(
            summary_layout, f"{self.session.connection_name}连接 / 最近检测", 0, 2
        )
        self._candidate_gate = self._add_summary_item(summary_layout, "候选状态", 1, 0)
        self._phase = self._add_summary_item(summary_layout, "当前阶段", 1, 1)
        root.addWidget(self._summary_card)
''',
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    '''        self._interrupt_card = QFrame()
        self._interrupt_card.setObjectName("interruptCard")
        self._interrupt_card.setMaximumHeight(138)
''',
    '''        self._interrupt_card = QFrame()
        self._interrupt_card.setObjectName("interruptCard")
        self._interrupt_card.setMinimumHeight(112)
        self._interrupt_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
''',
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    "        self._cards_scroll.setMinimumHeight(280)\n",
    "        self._cards_scroll.setMinimumHeight(220)\n",
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    '''        actions.addWidget(self._secondary_action, 1)
        root.addLayout(actions)
        self._primary_action.clicked.connect(self._primary_clicked)
''',
    '''        actions.addWidget(self._secondary_action, 1)
        for button in (
            self._primary_action,
            self._manual_fetch_action,
            self._secondary_action,
        ):
            button.setMinimumHeight(44)
        root.addLayout(actions)
        self._primary_action.clicked.connect(self._primary_clicked)
''',
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    '''        caption = QLabel(label)
        caption.setObjectName("summaryLabel")
        value = QLabel()
        value.setObjectName("summaryValue")
        value.setWordWrap(True)
''',
    '''        caption = QLabel(label)
        caption.setObjectName("summaryLabel")
        caption.setWordWrap(True)
        value = QLabel()
        value.setObjectName("summaryValue")
        value.setWordWrap(True)
        value.setMinimumHeight(38)
        value.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
''',
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    '''        summary = QAction("盘后回顾与PDF", self)
        summary.triggered.connect(self._open_daily_summary)
        settings.addAction(summary)

        if not getattr(self.session, "advanced_diagnostics", True):
''',
    '''        summary = QAction("盘后回顾与PDF", self)
        summary.triggered.connect(self._open_daily_summary)
        settings.addAction(summary)
        if sys.platform != "darwin":
            settings.addSeparator()
            quit_action = QAction("退出 StockWatcher", self)
            quit_action.setShortcut(QKeySequence.StandardKey.Quit)
            quit_action.triggered.connect(self._quit_application)
            settings.addAction(quit_action)

        if not getattr(self.session, "advanced_diagnostics", True):
''',
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    '''        elif connection is TqConnectionState.CONNECTED:
            self._page_title.setText("当前观察" if healthy else "数据接口已连接")
''',
    '''        elif connection is TqConnectionState.CONNECTED:
            self._page_title.setText(
                "当前观察" if healthy else "数据接口已连接，正在准备"
            )
''',
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    '''            self._open_detail_by_code,
            parent=self,
        )
''',
    '''            self._open_detail_by_code,
            parent=self,
            open_list_callback=self.restore_main_window,
        )
''',
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    '''    def request_application_exit(self) -> None:
        """Allow the explicit application-menu Quit action to close resources."""
        self._mac_window_close_policy.request_application_exit()

    def restore_main_window(self) -> None:
''',
    '''    def request_application_exit(self) -> None:
        """Allow an explicit Quit action to close resources."""
        self._mac_window_close_policy.request_application_exit()

    def _quit_application(self) -> None:
        self.request_application_exit()
        self.close()

    def restore_main_window(self) -> None:
''',
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    '''    @Slot()
    def _on_tq_operation_finished(self) -> None:
        self._operation_progress_timer.stop()
        self._active_operation = None
        self._refresh()
''',
    '''    @Slot()
    def _on_tq_operation_finished(self) -> None:
        self._operation_progress_timer.stop()
        self._active_operation = None
        if self._closing:
            return
        self._refresh()
''',
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    '''    @Slot()
    def _on_tq_thread_finished(self) -> None:
        self._operation_thread = None
        self._operation_worker = None
        if self._queued_manual_fetch:
            self._queued_manual_fetch = False
            self._manual_fetch_tq()
''',
    '''    @Slot()
    def _on_tq_thread_finished(self) -> None:
        self._operation_thread = None
        self._operation_worker = None
        if self._closing:
            self._queued_manual_fetch = False
            self._finalize_shutdown()
            if self._close_after_worker:
                self._close_after_worker = False
                QTimer.singleShot(0, self.close)
            return
        if self._queued_manual_fetch:
            self._queued_manual_fetch = False
            self._manual_fetch_tq()
''',
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    '''    def closeEvent(self, event: QCloseEvent) -> None:
        # A spontaneous close is the user clicking the macOS red button (hide);
        # a non-spontaneous close is QApplication::closeAllWindows(), which is
        # how Qt handles the quit AppleEvent (Dock Quit, logout, osascript) -
        # that must really exit instead of being swallowed by the hide policy.
        self._closing = True
        if (
            self._mac_window_close_policy.should_hide_on_close
            and event.spontaneous()
        ):
            self._closing = False
            event.ignore()
            self.hide()
            return
        self._auto_check_timer.stop()
        self._operation_progress_timer.stop()
        self._heartbeat_timer.stop()
        self._summary_check_timer.stop()
        self._queued_manual_fetch = False
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
''',
    '''    def _prepare_for_close(self) -> None:
        self._auto_check_timer.stop()
        self._operation_progress_timer.stop()
        self._heartbeat_timer.stop()
        self._summary_check_timer.stop()
        self._queued_manual_fetch = False
        if self._popup is not None:
            self._popup.close()
            self._popup = None
        if self._initial_data_source_dialog is not None:
            self._initial_data_source_dialog.close()
            self._initial_data_source_dialog = None

    def _request_session_shutdown(self) -> None:
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        request = getattr(self.session, "request_shutdown", None)
        if callable(request):
            request()

    def _finalize_shutdown(self) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        shutdown = getattr(self.session, "shutdown", None)
        if callable(shutdown):
            shutdown()

    def closeEvent(self, event: QCloseEvent) -> None:
        # A spontaneous close is the user clicking the macOS red button (hide);
        # a non-spontaneous close is QApplication::closeAllWindows(), which is
        # how Qt handles the quit AppleEvent (Dock Quit, logout, osascript) -
        # that must really exit instead of being swallowed by the hide policy.
        self._closing = True
        if (
            self._mac_window_close_policy.should_hide_on_close
            and event.spontaneous()
        ):
            self._closing = False
            event.ignore()
            self.hide()
            return

        self._prepare_for_close()
        self._request_session_shutdown()
        thread = self._operation_thread
        if thread is not None and thread.isRunning():
            # Do not block the GUI thread on a network/scan worker. Hide now,
            # request cooperative cancellation, and finalize on finished.
            self._close_after_worker = True
            event.ignore()
            self.hide()
            request_interruption = getattr(thread, "requestInterruption", None)
            if callable(request_interruption):
                request_interruption()
            thread.quit()
            return

        self._finalize_shutdown()
        event.accept()
''',
)

# Popup activation and complete-rectangle clamping.
replace_once(
    "src/stock_watcher/ui/popup.py",
    "from PySide6.QtCore import QPoint, QSettings, Qt, Signal\n",
    "from PySide6.QtCore import QPoint, QRect, QSettings, Qt, Signal\n",
)
replace_once(
    "src/stock_watcher/ui/popup.py",
    '''        details_callback: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
''',
    '''        details_callback: Callable[[str], None],
        parent: QWidget | None = None,
        *,
        open_list_callback: Callable[[], None] | None = None,
    ) -> None:
''',
)
replace_once(
    "src/stock_watcher/ui/popup.py",
    '''        self.setObjectName("alertPopup")
        self.setFixedWidth(460)
        self._title = title
''',
    '''        self.setObjectName("alertPopup")
        self._title = title
        self._open_list_callback = open_list_callback
''',
)
replace_once(
    "src/stock_watcher/ui/popup.py",
    '''        open_list = QPushButton("打开列表")
        open_list.setObjectName("primaryButton")
        open_list.clicked.connect(self.close)
''',
    '''        open_list = QPushButton("打开列表")
        open_list.setObjectName("primaryButton")
        open_list.clicked.connect(self._open_list)
''',
)
replace_once(
    "src/stock_watcher/ui/popup.py",
    '''    def show_at_bottom_right(self, *, preferred_screen: QScreen | None = None) -> None:
        stored = self._settings.value("alert/position")
        if isinstance(stored, QPoint) and any(
            screen.availableGeometry().contains(stored)
            for screen in QGuiApplication.screens()
        ):
            self.move(stored)
            self.show()
            return
        # A popup created as a Tool window does not reliably inherit its
        # parent's display on macOS.  Prefer the main window's current screen
        # so a multi-monitor user sees all three candidates beside the app.
        screen = preferred_screen or self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 18, area.bottom() - self.height() - 18)
        self.show()
''',
    '''    def _open_list(self) -> None:
        if self._open_list_callback is not None:
            self._open_list_callback()
        self.close()

    @staticmethod
    def _clamp_point(
        area: QRect,
        desired: QPoint,
        *,
        width: int,
        height: int,
    ) -> QPoint:
        maximum_x = max(area.left(), area.right() - width + 1)
        maximum_y = max(area.top(), area.bottom() - height + 1)
        return QPoint(
            min(max(desired.x(), area.left()), maximum_x),
            min(max(desired.y(), area.top()), maximum_y),
        )

    def show_at_bottom_right(self, *, preferred_screen: QScreen | None = None) -> None:
        screens = QGuiApplication.screens()
        stored = self._settings.value("alert/position")
        stored_screen = (
            next(
                (
                    candidate
                    for candidate in screens
                    if isinstance(stored, QPoint)
                    and candidate.availableGeometry().contains(stored)
                ),
                None,
            )
            if isinstance(stored, QPoint)
            else None
        )
        screen = (
            stored_screen
            or preferred_screen
            or self.screen()
            or QGuiApplication.primaryScreen()
        )
        if screen is None:
            self.show()
            return
        area = screen.availableGeometry()
        target_width = max(260, min(460, area.width() - 36))
        self.setFixedWidth(target_width)
        self.adjustSize()
        desired = (
            stored
            if isinstance(stored, QPoint)
            else QPoint(
                area.right() - self.width() - 17,
                area.bottom() - self.height() - 17,
            )
        )
        self.move(
            self._clamp_point(
                area,
                desired,
                width=self.width(),
                height=self.height(),
            )
        )
        self.show()
''',
)
