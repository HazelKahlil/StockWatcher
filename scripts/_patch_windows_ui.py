from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGED: set[str] = set()


def _path(relative: str) -> Path:
    return ROOT / relative


def _read(relative: str) -> str:
    return _path(relative).read_text(encoding="utf-8")


def _write(relative: str, content: str) -> None:
    target = _path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    CHANGED.add(relative)


def replace_once(relative: str, old: str, new: str) -> None:
    content = _read(relative)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(
            f"{relative}: expected one match, found {count}: {old[:120]!r}"
        )
    _write(relative, content.replace(old, new, 1))


def regex_once(
    relative: str,
    pattern: str,
    replacement: str,
    *,
    flags: int = 0,
) -> None:
    content = _read(relative)
    updated, count = re.subn(
        pattern,
        lambda _match: replacement,
        content,
        count=1,
        flags=flags,
    )
    if count != 1:
        raise RuntimeError(
            f"{relative}: expected one regex match, found {count}: {pattern[:120]!r}"
        )
    _write(relative, updated)


def replace_all(relative: str, old: str, new: str, *, minimum: int = 1) -> None:
    content = _read(relative)
    count = content.count(old)
    if count < minimum:
        raise RuntimeError(
            f"{relative}: expected at least {minimum} matches, found {count}: {old!r}"
        )
    _write(relative, content.replace(old, new))


def insert_before(relative: str, marker: str, addition: str) -> None:
    replace_once(relative, marker, addition + marker)


# ---------------------------------------------------------------------------
# Windows-safe credential storage labels and native-backend enforcement.
# ---------------------------------------------------------------------------
replace_once(
    "src/stock_watcher/security/credential_store.py",
    '''    @property
    def storage_label(self) -> str:
        return "系统钥匙串" if self.platform == "darwin" else "系统安全存储"
''',
    '''    @property
    def storage_label(self) -> str:
        if self.platform == "darwin":
            return "系统钥匙串"
        if self.platform == "win32":
            return "Windows 凭据管理器"
        return "系统安全存储"
''',
)
replace_once(
    "src/stock_watcher/security/credential_store.py",
    '''        if self.platform == "darwin" and not _is_macos_keychain_backend(backend):
            raise CredentialStoreBackendError(
                "系统钥匙串不可用；请检查 macOS Keychain 后再保存 Token。"
            )
        return CredentialStoreBackendStatus(
''',
    '''        if self.platform == "darwin" and not _is_macos_keychain_backend(backend):
            raise CredentialStoreBackendError(
                "系统钥匙串不可用；请检查 macOS Keychain 后再保存 Token。"
            )
        if self.platform == "win32" and not _is_windows_credential_backend(backend):
            raise CredentialStoreBackendError(
                "Windows 凭据管理器不可用；请修复系统凭据后再保存 Token。"
            )
        return CredentialStoreBackendStatus(
''',
)
insert_before(
    "src/stock_watcher/security/credential_store.py",
    "\n\n@dataclass(slots=True)\nclass MemoryCredentialStore:",
    '''

def _is_windows_credential_backend(backend: object) -> bool:
    """Accept only the native Windows Credential Manager backend or a chainer containing it."""

    module = type(backend).__module__.casefold()
    if module.startswith("keyring.backends.windows"):
        return True
    chained = getattr(backend, "backends", ())
    if isinstance(chained, (list, tuple)):
        return any(_is_windows_credential_backend(item) for item in chained)
    return False
''',
)


# ---------------------------------------------------------------------------
# Bound native SDK calls so a stuck supplier request cannot keep Windows open.
# ---------------------------------------------------------------------------
replace_once(
    "src/stock_watcher/config/data_sources.py",
    '''    min_interval_seconds: float = Field(default=1.0, ge=0.6, le=30)
    stale_after_seconds: float = Field(default=60.0, gt=0, le=120)
''',
    '''    min_interval_seconds: float = Field(default=1.0, ge=0.6, le=30)
    request_timeout_seconds: float = Field(default=20.0, ge=5.0, le=60)
    stale_after_seconds: float = Field(default=60.0, gt=0, le=120)
''',
)
replace_once(
    "src/stock_watcher/providers/tushare/native_realtime_transport.py",
    "from io import StringIO\nfrom typing import Protocol, cast\n",
    "from io import StringIO\nfrom queue import Empty, Queue\nfrom typing import Protocol, cast\n",
)
replace_once(
    "src/stock_watcher/providers/tushare/native_realtime_transport.py",
    '''        self._lock = threading.Lock()
        self._request_budget = request_budget or ApplicationRequestBudget(
''',
    '''        self._lock = threading.Lock()
        self._timed_out_thread: threading.Thread | None = None
        self._request_budget = request_budget or ApplicationRequestBudget(
''',
)
insert_before(
    "src/stock_watcher/providers/tushare/native_realtime_transport.py",
    "    def execute(self, request: TransportRequest) -> TransportResult:\n",
    '''    def _fetch_with_timeout(
        self,
        batch: tuple[str, ...],
    ) -> list[dict[str, object]] | None:
        """Run the third-party SDK on a bounded daemon thread.

        The SDK does not expose a request timeout or cancellation handle. A daemon
        wrapper keeps the Qt scan worker bounded; after a timeout, new calls fail
        closed until the timed-out SDK invocation has actually returned.
        """

        result_queue: Queue[tuple[str, object]] = Queue(maxsize=1)

        def invoke() -> None:
            try:
                rows = self._client.fetch(batch, source=self.profile.source)
            except Exception as error:  # noqa: BLE001 - mapped to a safe provider reason
                result_queue.put(("error", error))
            else:
                result_queue.put(("ok", rows))

        thread = threading.Thread(
            target=invoke,
            name="stockwatcher-native-realtime-call",
            daemon=True,
        )
        thread.start()
        try:
            state, payload = result_queue.get(
                timeout=float(self.profile.request_timeout_seconds)
            )
        except Empty:
            self._timed_out_thread = thread
            raise ProviderError(ProviderFailureReason.TIMEOUT) from None
        self._timed_out_thread = None
        if state == "error":
            if isinstance(payload, Exception):
                raise payload
            raise RuntimeError("native realtime call failed without an exception")
        return cast(list[dict[str, object]] | None, payload)

''',
)
replace_once(
    "src/stock_watcher/providers/tushare/native_realtime_transport.py",
    '''        with self._lock:
            self._client.configure(token, str(self.profile.verify_url).rstrip("/"))
''',
    '''        with self._lock:
            timed_out = self._timed_out_thread
            if timed_out is not None:
                if timed_out.is_alive():
                    raise ProviderError(ProviderFailureReason.TIMEOUT)
                self._timed_out_thread = None
            self._client.configure(token, str(self.profile.verify_url).rstrip("/"))
''',
)
replace_once(
    "src/stock_watcher/providers/tushare/native_realtime_transport.py",
    "                    rows = self._client.fetch(batch, source=self.profile.source)\n",
    "                    rows = self._fetch_with_timeout(batch)\n",
)


# ---------------------------------------------------------------------------
# Platform-native font selection and clearer disabled controls.
# ---------------------------------------------------------------------------
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
    "#secondaryButton:disabled { color: #6f7d91; background: #edf1f6; border-color: #cfd8e4; }\n",
)
replace_once(
    "src/stock_watcher/ui/app.py",
    '''#cardsScroll, #cardsScroll > QWidget > QWidget, #cardsHost {
    background: transparent; border: none;
}
''',
    '''#pageScroll, #pageScroll > QWidget > QWidget, #pageHost,
#cardsScroll, #cardsScroll > QWidget > QWidget, #cardsHost {
    background: transparent; border: none;
}
''',
)
insert_before(
    "src/stock_watcher/ui/app.py",
    "def application_icon_path() -> Path:\n",
    '''def application_font_candidates(platform: str = sys.platform) -> tuple[str, ...]:
    """Return installed-family preferences for each supported desktop platform."""

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
    """Select an installed native UI font without forcing a missing macOS family."""

    installed = {family.casefold(): family for family in QFontDatabase.families()}
    font = app.font()
    for candidate in application_font_candidates(platform):
        selected = installed.get(candidate.casefold())
        if selected is not None:
            font.setFamily(selected)
            break
    app.setFont(font)
    return font.family()


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


# ---------------------------------------------------------------------------
# Main-window layout, popup lifecycle, explicit Windows quit, and nonblocking close.
# ---------------------------------------------------------------------------
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
    '''        self._popup: AlertPopup | None = None
        self._rows: dict[str, CandidateRow] = {}
''',
    '''        self._popup: AlertPopup | None = None
        self._popup_generation = 0
        self._rows: dict[str, CandidateRow] = {}
''',
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
        self.setMinimumSize(700, 420)
''',
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    '''        central = QWidget()
        root = QVBoxLayout(central)
''',
    '''        page = QWidget()
        page.setObjectName("pageHost")
        page.setMinimumWidth(660)
        root = QVBoxLayout(page)
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
        self._summary_card.setMinimumHeight(128)
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
        self._interrupt_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
''',
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    "        self._cards_scroll.setMinimumHeight(280)\n",
    "        self._cards_scroll.setMinimumHeight(180)\n",
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    '''        actions.addWidget(self._secondary_action, 1)
        root.addLayout(actions)
''',
    '''        actions.addWidget(self._secondary_action, 1)
        for button in (
            self._primary_action,
            self._manual_fetch_action,
            self._secondary_action,
        ):
            button.setMinimumHeight(44)
        root.addLayout(actions)
''',
)
replace_once(
    "src/stock_watcher/ui/main_window.py",
    '''        root.addLayout(footer)
        self.setCentralWidget(central)
''',
    '''        root.addLayout(footer)
        page_scroll = QScrollArea()
        page_scroll.setObjectName("pageScroll")
        page_scroll.setWidgetResizable(True)
        page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        page_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        page_scroll.setWidget(page)
        self.setCentralWidget(page_scroll)
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
regex_once(
    "src/stock_watcher/ui/main_window.py",
    r"    def _show_alert\(\n.*?\n    def enable_background_close",
    '''    def _close_popup(self) -> None:
        popup = self._popup
        if popup is None:
            return
        self._popup_generation += 1
        self._popup = None
        try:
            popup.close()
        except RuntimeError:
            # WA_DeleteOnClose may already have deleted the C++ object.
            pass

    def _show_alert(
        self,
        snapshot: UiSnapshot,
        *,
        title: str,
        subtitle: str | None = None,
        force: bool = False,
    ) -> None:
        signature = tuple(row.code for row in snapshot.candidates)
        if not force and signature == self._last_alert_signature:
            return
        self._last_alert_signature = signature
        self._close_popup()
        overall = (
            "偏弱"
            if snapshot.overall_label == "本轮整体偏弱"
            else snapshot.overall_label
        )
        alert_subtitle = subtitle or f"{format_time(snapshot.last_updated)} · {overall}"
        self._popup_generation += 1
        generation = self._popup_generation
        popup = AlertPopup(
            snapshot.candidates,
            title,
            alert_subtitle,
            self._open_detail_by_code,
            parent=self,
            open_list_callback=self.restore_main_window,
        )

        def clear_popup_reference(_destroyed: QObject | None = None) -> None:
            if self._popup_generation == generation:
                self._popup = None

        popup.destroyed.connect(clear_popup_reference)
        self._popup = popup
        QApplication.beep()
        popup.show_at_bottom_right(preferred_screen=self.screen())
        if self._secondary_notification is not None:
            self._secondary_notification(title, alert_subtitle)

    def enable_background_close''',
    flags=re.DOTALL,
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
    '''    def _stop_replay(self) -> None:
        self.session.stop()
        if self._popup is not None:
            self._popup.close()
            self._popup = None
        self._refresh()
''',
    '''    def _stop_replay(self) -> None:
        self.session.stop()
        self._close_popup()
        self._refresh()
''',
)
regex_once(
    "src/stock_watcher/ui/main_window.py",
    r"    def closeEvent\(self, event: QCloseEvent\) -> None:\n.*?        event\.accept\(\)\n?$",
    '''    def _prepare_for_close(self) -> None:
        self._auto_check_timer.stop()
        self._operation_progress_timer.stop()
        self._heartbeat_timer.stop()
        self._summary_check_timer.stop()
        self._queued_manual_fetch = False
        self._close_popup()
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
            # Never block the GUI thread on requests/SDK work. The window closes
            # visually at once and the cooperative cancellation path owns cleanup.
            self._close_after_worker = True
            event.ignore()
            self.hide()
            thread.requestInterruption()
            thread.quit()
            return

        self._finalize_shutdown()
        event.accept()
''',
    flags=re.DOTALL,
)


# ---------------------------------------------------------------------------
# Popup geometry: keep the whole window on-screen and make “打开列表” truthful.
# ---------------------------------------------------------------------------
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
        self._settings = QSettings("StockWatcher", "StockWatcher")
''',
    '''        self.setObjectName("alertPopup")
        self.setMinimumWidth(320)
        self._title = title
        self._open_list_callback = open_list_callback
        self._settings = QSettings("StockWatcher", "StockWatcher")
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
regex_once(
    "src/stock_watcher/ui/popup.py",
    r"    def show_at_bottom_right\(self, \*, preferred_screen: QScreen \| None = None\) -> None:\n.*?\n    def closeEvent",
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
        target_width = max(280, min(460, area.width() - 36))
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

    def closeEvent''',
    flags=re.DOTALL,
)


# ---------------------------------------------------------------------------
# Token tests run off the Qt GUI thread; closing invalidates in-flight secrets.
# ---------------------------------------------------------------------------
replace_once(
    "src/stock_watcher/ui/data_source_settings.py",
    "from threading import Lock\n",
    "from threading import Lock, Thread\n",
)
replace_once(
    "src/stock_watcher/ui/data_source_settings.py",
    "    QPushButton,\n    QSizePolicy,\n",
    "    QPushButton,\n    QScrollArea,\n    QSizePolicy,\n",
)
replace_once(
    "src/stock_watcher/ui/data_source_settings.py",
    '''    _pending: dict[str, PendingCredential] = field(default_factory=dict)
    _last_results: dict[str, CredentialTestResult] = field(default_factory=dict)
    _test_lock: Lock = field(default_factory=Lock)
''',
    '''    _pending: dict[str, PendingCredential] = field(default_factory=dict)
    _last_results: dict[str, CredentialTestResult] = field(default_factory=dict)
    _test_lock: Lock = field(default_factory=Lock)
    _pending_state_lock: Lock = field(default_factory=Lock)
    _pending_epoch: int = 0
''',
)
replace_once(
    "src/stock_watcher/ui/data_source_settings.py",
    '''        try:
            return self._test_candidate_locked(
                name,
                secret,
                base_url=base_url,
                use_system_proxy=use_system_proxy,
            )
''',
    '''        try:
            with self._pending_state_lock:
                pending_epoch = self._pending_epoch
            return self._test_candidate_locked(
                name,
                secret,
                base_url=base_url,
                use_system_proxy=use_system_proxy,
                pending_epoch=pending_epoch,
            )
''',
)
replace_once(
    "src/stock_watcher/ui/data_source_settings.py",
    '''        *,
        base_url: str,
        use_system_proxy: bool,
    ) -> CredentialTestResult:
        if not secret:
''',
    '''        *,
        base_url: str,
        use_system_proxy: bool,
        pending_epoch: int,
    ) -> CredentialTestResult:
        if not secret:
''',
)
replace_once(
    "src/stock_watcher/ui/data_source_settings.py",
    '''            self._last_results[name] = result
            return result
        profile = HttpProfile.model_validate(
''',
    '''            self._stage_test_result(
                name,
                secret,
                result,
                self.profile(name),
                pending_epoch=pending_epoch,
            )
            return result
        profile = HttpProfile.model_validate(
''',
)
replace_once(
    "src/stock_watcher/ui/data_source_settings.py",
    '''        result = self.tester.test(profile, secret)
        self._last_results[name] = result
        if result.success:
            self._pending[name] = PendingCredential(
                secret=secret,
                result=result,
                profile=profile,
            )
        else:
            self._pending.pop(name, None)
        return result

    def commit_candidate(self, name: str, *, confirmed: bool) -> bool:
''',
    '''        result = self.tester.test(profile, secret)
        self._stage_test_result(
            name,
            secret,
            result,
            profile,
            pending_epoch=pending_epoch,
        )
        return result

    def _stage_test_result(
        self,
        name: str,
        secret: str,
        result: CredentialTestResult,
        profile: HttpProfile,
        *,
        pending_epoch: int,
    ) -> None:
        with self._pending_state_lock:
            if pending_epoch != self._pending_epoch:
                return
            self._last_results[name] = result
            if result.success:
                self._pending[name] = PendingCredential(
                    secret=secret,
                    result=result,
                    profile=profile,
                )
            else:
                pending = self._pending.pop(name, None)
                if pending is not None:
                    pending.secret = ""

    def commit_candidate(self, name: str, *, confirmed: bool) -> bool:
''',
)
replace_once(
    "src/stock_watcher/ui/data_source_settings.py",
    '''    def clear_credential(self, name: str) -> bool:
        self._pending.pop(name, None)
        try:
''',
    '''    def clear_credential(self, name: str) -> bool:
        with self._pending_state_lock:
            pending = self._pending.pop(name, None)
            if pending is not None:
                pending.secret = ""
        try:
''',
)
replace_once(
    "src/stock_watcher/ui/data_source_settings.py",
    '''    def discard_pending(self) -> None:
        for pending in self._pending.values():
            pending.secret = ""
        self._pending.clear()
''',
    '''    def discard_pending(self) -> None:
        with self._pending_state_lock:
            self._pending_epoch += 1
            for pending in self._pending.values():
                pending.secret = ""
            self._pending.clear()
''',
)
replace_once(
    "src/stock_watcher/ui/data_source_settings.py",
    '''        self.setObjectName("dataSourceEditor")
        self.controller = controller
        profile = controller.profile("primary")
        layout = QVBoxLayout(self)
''',
    '''        self.setObjectName("dataSourceEditor")
        self.controller = controller
        self._test_generation = 0
        self._test_result_lock = Lock()
        self._test_result: tuple[int, CredentialTestResult] | None = None
        self._test_poll_timer = QTimer(self)
        self._test_poll_timer.setInterval(25)
        self._test_poll_timer.timeout.connect(self._poll_candidate_test)
        profile = controller.profile("primary")
        layout = QVBoxLayout(self)
''',
)
regex_once(
    "src/stock_watcher/ui/data_source_settings.py",
    r"    def _test_and_save\(self\) -> None:\n.*?\n    def _recheck",
    '''    def _test_and_save(self) -> None:
        secret = self.secret.text()
        base_url = (
            self.address.text().strip()
            if self.address is not None
            else self._base_url
        )
        use_system_proxy = (
            bool(self.proxy.currentData())
            if self.proxy is not None
            else self._use_system_proxy
        )
        if not secret:
            result = self.controller.test_candidate(
                "primary",
                secret,
                base_url=base_url,
                use_system_proxy=use_system_proxy,
            )
            self._apply_candidate_test_result(result)
            return
        self._test_generation += 1
        generation = self._test_generation
        with self._test_result_lock:
            self._test_result = None
        self._set_test_busy(True)
        self.status.setText("正在后台测试基础连接；窗口仍可关闭。")
        Thread(
            target=self._run_candidate_test,
            args=(generation, secret, base_url, use_system_proxy),
            name="stockwatcher-token-test",
            daemon=True,
        ).start()
        self._test_poll_timer.start()

    def _run_candidate_test(
        self,
        generation: int,
        secret: str,
        base_url: str,
        use_system_proxy: bool,
    ) -> None:
        try:
            result = self.controller.test_candidate(
                "primary",
                secret,
                base_url=base_url,
                use_system_proxy=use_system_proxy,
            )
        except Exception as error:  # noqa: BLE001 - publish only the safe class name
            result = CredentialTestResult(
                success=False,
                tested_at=datetime.now().astimezone(),
                status_text="基础连接测试异常，当前 Token 未被替换。",
                permission_summary=f"安全存储或网络检查失败：{type(error).__name__}",
                expires_at="未知",
                safe_reason="business_error",
            )
        with self._test_result_lock:
            if generation == self._test_generation:
                self._test_result = (generation, result)

    def _poll_candidate_test(self) -> None:
        with self._test_result_lock:
            value = self._test_result
            self._test_result = None
        if value is None:
            return
        generation, result = value
        if generation != self._test_generation:
            return
        self._test_poll_timer.stop()
        self._set_test_busy(False)
        self._apply_candidate_test_result(result)

    def _apply_candidate_test_result(self, result: CredentialTestResult) -> None:
        self.status.setText(result.status_text)
        self.last_test.setText(result.tested_at.strftime("%Y-%m-%d %H:%M:%S"))
        self.permission.setText(result.permission_summary)
        if result.success:
            answer = QMessageBox.question(
                self,
                "确认保存 Token",
                "连接测试通过。确认安全保存并重新预热实时数据吗？",
            )
            if self.controller.commit_candidate(
                "primary",
                confirmed=answer == QMessageBox.StandardButton.Yes,
            ):
                self.secret.clear()
                self.status.setText("Token 已保存；正在后台分项检测并重新预热数据")
                self.controller.start_capability_checks()
                self._refresh_capabilities()
            elif answer == QMessageBox.StandardButton.Yes:
                self.status.setText("保存失败；原 Token 保持不变")
        self._set_test_busy(False)

    def _set_test_busy(self, busy: bool) -> None:
        self.save_button.setEnabled(not busy)
        self.secret.setEnabled(not busy)
        if busy:
            self.recheck_button.setEnabled(False)
            self.clear_button.setEnabled(False)
        else:
            self._refresh_capabilities()

    def cancel_pending_test(self) -> None:
        self._test_generation += 1
        self._test_poll_timer.stop()
        with self._test_result_lock:
            self._test_result = None
        self._set_test_busy(False)

    def _recheck''',
    flags=re.DOTALL,
)
regex_once(
    "src/stock_watcher/ui/data_source_settings.py",
    r"class DataSourceSettingsDialog\(QDialog\):\n    def __init__\(.*?\n    def _migrate_legacy",
    '''class DataSourceSettingsDialog(QDialog):
    def __init__(
        self,
        controller: DataSourceSettingsController | None = None,
        parent: QWidget | None = None,
        *,
        platform: str = sys.platform,
    ) -> None:
        super().__init__(parent)
        self.controller = controller or DataSourceSettingsController()
        self.setWindowTitle("数据接口")
        self.setMinimumSize(620, 460)
        self.resize(760, 640)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 26, 28, 24)
        root.setSpacing(16)

        title = QLabel("数据接口")
        title.setObjectName("dialogTitle")
        description = QLabel(
            "接口配置已内置，只需一个 Tushare Token。"
            f"Token 仅保存在{self.controller.credential_storage_label}，"
            "测试失败不会替换当前 Token。"
        )
        description.setObjectName("dialogDescription")
        description.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(description)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(14)
        show_advanced_diagnostics = platform != "darwin"
        self._primary_editor = _PrimaryEditor(
            self.controller,
            show_advanced_settings=show_advanced_diagnostics,
        )
        content_layout.addWidget(self._primary_editor)

        if (
            not self.controller.credential_present("primary")
            and self.controller.credential_present("fast")
        ):
            migrate = QPushButton("迁移本机旧 Token")
            migrate.setObjectName("secondaryButton")
            migrate.setToolTip("会先测试旧 Token，成功后才复制到统一凭据位置")
            migrate.clicked.connect(self._migrate_legacy)
            content_layout.addWidget(migrate)

        self.mode: QComboBox | None = None
        if show_advanced_diagnostics:
            diagnostics = QGroupBox("高级诊断")
            diagnostics.setCheckable(True)
            diagnostics.setChecked(False)
            diagnostic_layout = QFormLayout(diagnostics)
            self.mode = QComboBox()
            for label, value in (
                ("Tushare 数据接口", DataSourceMode.TUSHARE_15000),
                ("Replay", DataSourceMode.REPLAY),
                ("旧接口诊断", DataSourceMode.ADVANCED_DIAGNOSTIC),
                ("通达信诊断", DataSourceMode.TDX_DIAGNOSTIC),
            ):
                self.mode.addItem(label, value)
            current = self.mode.findData(self.controller.settings.mode)
            self.mode.setCurrentIndex(max(0, current))
            self.mode.currentIndexChanged.connect(self._mode_changed)
            diagnostic_layout.addRow("运行方式", self.mode)
            self.mode.setVisible(False)
            diagnostics.toggled.connect(self.mode.setVisible)
            content_layout.addWidget(diagnostics)
        content_layout.addStretch()

        scroll = QScrollArea()
        scroll.setObjectName("dataSourceScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        close = QPushButton("关闭")
        close.setObjectName("secondaryButton")
        close.clicked.connect(self.accept)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def _migrate_legacy''',
    flags=re.DOTALL,
)
replace_once(
    "src/stock_watcher/ui/data_source_settings.py",
    '''    def closeEvent(self, event: QCloseEvent) -> None:
        self.controller.discard_pending()
        super().closeEvent(event)
''',
    '''    def done(self, result: int) -> None:
        self._primary_editor.cancel_pending_test()
        self.controller.discard_pending()
        super().done(result)
''',
)


# ---------------------------------------------------------------------------
# History/outcome reads use daemon Python workers, so dialog close never waits.
# ---------------------------------------------------------------------------
replace_once(
    "src/stock_watcher/ui/history.py",
    "from typing import Any\n\nfrom PySide6.QtCore import QThread, Signal\nfrom PySide6.QtGui import QCloseEvent\n",
    "from threading import Lock, Thread\nfrom typing import Any\n\nfrom PySide6.QtCore import QTimer\n",
)
regex_once(
    "src/stock_watcher/ui/history.py",
    r"class HistoryWorker\(QThread\):\n.*?\n\nclass HistoryDialog",
    "class HistoryDialog",
    flags=re.DOTALL,
)
replace_once(
    "src/stock_watcher/ui/history.py",
    '''        self.resize(860, 720)
        self._worker = HistoryWorker(path)
        root = QVBoxLayout(self)
''',
    '''        self.resize(860, 720)
        self._path = path
        self._load_generation = 0
        self._load_lock = Lock()
        self._load_result: tuple[int, object, str] | None = None
        self._load_timer = QTimer(self)
        self._load_timer.setInterval(25)
        self._load_timer.timeout.connect(self._poll_history_load)
        root = QVBoxLayout(self)
''',
)
replace_once(
    "src/stock_watcher/ui/history.py",
    '''        self._worker.loaded.connect(self._on_loaded)
        self._worker.start()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt override
        if self._worker.isRunning():
            self._worker.wait(2000)
        self._outcomes.wait_for_worker()
        super().closeEvent(event)

    def _on_loaded(self, rows: object, error: str) -> None:
''',
    '''        self._start_history_load()

    def _start_history_load(self) -> None:
        self._load_generation += 1
        generation = self._load_generation
        with self._load_lock:
            self._load_result = None
        Thread(
            target=self._read_history,
            args=(generation,),
            name="stockwatcher-history-read",
            daemon=True,
        ).start()
        self._load_timer.start()

    def _read_history(self, generation: int) -> None:
        try:
            store = SQLiteStore(self._path, read_only=True)
            rows: object = store.list_alert_history(
                now=datetime.now(SHANGHAI),
                days=30,
            )
            error = ""
        except Exception as exc:  # noqa: BLE001 - expose only the safe class name
            rows = []
            error = f"历史暂不可读：{type(exc).__name__}"
        with self._load_lock:
            if generation == self._load_generation:
                self._load_result = (generation, rows, error)

    def _poll_history_load(self) -> None:
        with self._load_lock:
            result = self._load_result
            self._load_result = None
        if result is None:
            return
        generation, rows, error = result
        if generation != self._load_generation:
            return
        self._load_timer.stop()
        self._on_loaded(rows, error)

    def done(self, result: int) -> None:
        self._load_generation += 1
        self._load_timer.stop()
        self._outcomes.cancel_pending_loads()
        super().done(result)

    def _on_loaded(self, rows: object, error: str) -> None:
''',
)

replace_once(
    "src/stock_watcher/ui/outcome_review.py",
    "from pathlib import Path\nfrom typing import Any\n\nfrom PySide6.QtCore import QThread, Signal\n",
    "from pathlib import Path\nfrom threading import Lock, Thread\nfrom typing import Any\n\nfrom PySide6.QtCore import QTimer\n",
)
regex_once(
    "src/stock_watcher/ui/outcome_review.py",
    r"class OutcomeReviewWorker\(QThread\):\n.*?\n\nclass OutcomeReviewPanel",
    "class OutcomeReviewPanel",
    flags=re.DOTALL,
)
replace_once(
    "src/stock_watcher/ui/outcome_review.py",
    '''        self._path = path
        self._worker: OutcomeReviewWorker | None = None
        self._range_buttons: dict[int | None, QPushButton] = {}
''',
    '''        self._path = path
        self._load_generation = 0
        self._load_lock = Lock()
        self._load_result: tuple[int, object, object, str] | None = None
        self._load_timer = QTimer(self)
        self._load_timer.setInterval(25)
        self._load_timer.timeout.connect(self._poll_load)
        self._range_buttons: dict[int | None, QPushButton] = {}
''',
)
regex_once(
    "src/stock_watcher/ui/outcome_review.py",
    r"    def load\(self, trading_days: int \| None\) -> None:\n.*?\n    def _on_loaded",
    '''    def load(self, trading_days: int | None) -> None:
        self._load_generation += 1
        generation = self._load_generation
        self._status.setText("正在读取次日复盘…")
        for days, button in self._range_buttons.items():
            button.setChecked(days == trading_days)
            button.setEnabled(False)
        with self._load_lock:
            self._load_result = None
        Thread(
            target=self._read_outcomes,
            args=(generation, trading_days),
            name="stockwatcher-outcome-read",
            daemon=True,
        ).start()
        self._load_timer.start()

    def _read_outcomes(self, generation: int, trading_days: int | None) -> None:
        try:
            store = SQLiteStore(self._path, read_only=True)
            records = candidate_outcome_rows(store, trading_days=trading_days)
            review: object = build_outcome_review(records)
            backfill: object = store.get_app_setting("candidate_outcome_backfill_status")
            error = ""
        except Exception as exc:  # noqa: BLE001 - shown safely in the page
            review = None
            backfill = None
            error = f"复盘暂不可读：{type(exc).__name__}"
        with self._load_lock:
            if generation == self._load_generation:
                self._load_result = (generation, review, backfill, error)

    def _poll_load(self) -> None:
        with self._load_lock:
            result = self._load_result
            self._load_result = None
        if result is None:
            return
        generation, value, backfill, error = result
        if generation != self._load_generation:
            return
        self._load_timer.stop()
        for button in self._range_buttons.values():
            button.setEnabled(True)
        self._on_loaded(value, backfill, error)

    def cancel_pending_loads(self) -> None:
        self._load_generation += 1
        self._load_timer.stop()
        with self._load_lock:
            self._load_result = None
        for button in self._range_buttons.values():
            button.setEnabled(True)

    def wait_for_worker(self, timeout_ms: int = 2000) -> None:
        # Backward-compatible API: cancellation is deliberately nonblocking.
        del timeout_ms
        self.cancel_pending_loads()

    def _on_loaded''',
    flags=re.DOTALL,
)


# ---------------------------------------------------------------------------
# Cooperative session shutdown and platform-correct secure-storage wording.
# ---------------------------------------------------------------------------
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    "from threading import Lock, Thread, current_thread\n",
    "from threading import Event, Lock, Thread, current_thread\n",
)
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    '''        self._credential_refresh_lock = Lock()
        self._credential_refresh_in_flight = False
''',
    '''        self._credential_refresh_lock = Lock()
        self._shutdown_event = Event()
        self._shutdown_lock = Lock()
        self._shutdown_complete = False
        self._credential_refresh_in_flight = False
''',
)
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    '''        self.app_badge = "Mac V1" if sys.platform == "darwin" else "Windows V1"
        self._alert_client_platform = (
''',
    '''        self.app_badge = "Mac V1" if sys.platform == "darwin" else "Windows V1"
        storage_label = getattr(self.credential_store, "storage_label", "系统安全存储")
        self._credential_storage_label = (
            storage_label if isinstance(storage_label, str) and storage_label else "系统安全存储"
        )
        self._alert_client_platform = (
''',
)
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    '''    def provider_changed(self, mode: DataSourceMode) -> None:
        self.settings = self.settings.model_copy(update={"mode": mode})
''',
    '''    def provider_changed(self, mode: DataSourceMode) -> None:
        if self._shutdown_event.is_set():
            return
        self.settings = self.settings.model_copy(update={"mode": mode})
''',
)
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    '''    def refresh_credential_state_async(self, callback: Callable[[], None] | None = None) -> None:
        """Read native Keychain off the GUI thread and publish a memory snapshot."""
''',
    '''    def refresh_credential_state_async(self, callback: Callable[[], None] | None = None) -> None:
        """Read the native credential backend off the GUI thread."""
        if self._shutdown_event.is_set():
            return
''',
)
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    "        Thread(target=read, name=\"stockwatcher-keychain\", daemon=True).start()\n",
    "        Thread(target=read, name=\"stockwatcher-credentials\", daemon=True).start()\n",
)
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    '''    def _poll_credential_state(self) -> None:
        with self._credential_refresh_lock:
''',
    '''    def _poll_credential_state(self) -> None:
        if self._shutdown_event.is_set():
            if self._credential_poll_timer is not None:
                self._credential_poll_timer.stop()
            return
        with self._credential_refresh_lock:
''',
)
insert_before(
    "src/stock_watcher/ui/tushare_v1_session.py",
    "    def stop(self) -> None:\n",
    '''    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_event.is_set()

    def request_shutdown(self) -> None:
        """Request cooperative cancellation without waiting on the GUI thread."""

        if self._shutdown_event.is_set():
            return
        self._shutdown_event.set()
        self._cancel_in_flight_scan()
        future = self._universe_future
        if future is not None:
            future.cancel()
        with self._outcome_future_lock:
            for outcome_future in self._outcome_futures:
                outcome_future.cancel()

''',
)
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    '''    def recover(self) -> None:
        self._run(force=False, manual_request=False)

    def begin_manual_fetch(self) -> None:
        now = _shanghai(self._clock())
''',
    '''    def recover(self) -> None:
        if self._shutdown_event.is_set():
            return
        self._run(force=False, manual_request=False)

    def begin_manual_fetch(self) -> None:
        if self._shutdown_event.is_set():
            return
        now = _shanghai(self._clock())
''',
)
regex_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    r"    def manual_fetch\(self\) -> None:\n.*?\n    def _manual_result_ready",
    '''    def manual_fetch(self) -> None:
        if self._shutdown_event.is_set():
            return
        started = monotonic_time()
        deadline = started + self.manual_fetch_timeout_seconds
        self._manual_started_monotonic = started
        self._manual_scan_round = 0
        try:
            while not self._shutdown_event.is_set():
                scan_ready = self._manual_scan_is_ready()
                if scan_ready:
                    self._set_manual_scan_progress(
                        self._manual_scan_round + 1,
                        deadline=deadline,
                    )
                outcome = self._run(force=True, manual_request=True)
                if self._shutdown_event.is_set():
                    return
                if outcome is not None:
                    self._manual_scan_round += 1
                if (
                    outcome is not None
                    and outcome.health is HealthState.HEALTHY
                    and self.batch is not None
                    and len(self.batch.candidates) == 3
                ):
                    if self._manual_result_ready(outcome, deadline=deadline):
                        self._publish_manual_result(outcome)
                        return
                if outcome is not None and outcome.health is HealthState.STOPPED:
                    return
                if not self._manual_should_wait():
                    return
                if monotonic_time() >= deadline:
                    self._set_manual_timeout()
                    return
                if self._shutdown_event.wait(0.2):
                    return
        finally:
            self._manual_started_monotonic = None

    def _manual_result_ready''',
    flags=re.DOTALL,
)
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    '''    ) -> ScanOutcome | None:
        now = _shanghai(self._clock())
''',
    '''    ) -> ScanOutcome | None:
        if self._shutdown_event.is_set():
            return None
        now = _shanghai(self._clock())
''',
)
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    '''        outcome = self._runtime.scan_once()
        if self._is_network_interrupted():
''',
    '''        outcome = self._runtime.scan_once()
        if self._shutdown_event.is_set():
            self._finish_scan_attempt(
                attempt_id,
                completed_at=_shanghai(self._clock()),
                state="cancelled",
                detail="application-shutdown",
            )
            return None
        if self._is_network_interrupted():
''',
)
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    '''    def _start_universe_refresh(self, now: datetime) -> bool:
        if self._runtime is None:
''',
    '''    def _start_universe_refresh(self, now: datetime) -> bool:
        if self._shutdown_event.is_set():
            return False
        if self._runtime is None:
''',
)
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    '''    def _publish_credential_state(
        self,
        generation: int,
''',
    '''    def _publish_credential_state(
        self,
        generation: int,
''',
)
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    '''    ) -> None:
        with self._credential_refresh_lock:
            if generation != self._credential_refresh_generation:
''',
    '''    ) -> None:
        if self._shutdown_event.is_set():
            return
        with self._credential_refresh_lock:
            if generation != self._credential_refresh_generation:
''',
)
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    '''        self.connection_detail = "正在读取系统钥匙串；读取完成前不会发起实时请求。"
        self.data_gate_label = "正在读取凭据"
        self.candidate_gate_label = "等待凭据检测"
        self.health_detail = self.connection_detail
        self.status_issues = ("Keychain 检查在后台执行，窗口保持可用。",)
''',
    '''        self.connection_detail = (
            f"正在读取{self._credential_storage_label}；读取完成前不会发起实时请求。"
        )
        self.data_gate_label = "正在读取凭据"
        self.candidate_gate_label = "等待凭据检测"
        self.health_detail = self.connection_detail
        self.status_issues = ("系统凭据检查在后台执行，窗口保持可用。",)
''',
)
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    '''        self.connection_detail = "系统钥匙串暂时不可用；未发起实时请求。"
        self.data_gate_label = "钥匙串不可用"
        self.candidate_gate_label = "等待钥匙串恢复"
        self.health_detail = self.connection_detail
        self.status_issues = ("请解锁 macOS 钥匙串或处理系统安全存储提示后重试。",)
''',
    '''        self.connection_detail = (
            f"{self._credential_storage_label}暂时不可用；未发起实时请求。"
        )
        self.data_gate_label = "安全存储不可用"
        self.candidate_gate_label = "等待安全存储恢复"
        self.health_detail = self.connection_detail
        self.status_issues = (
            "请处理系统凭据提示或修复安全存储后重新检测。",
        )
''',
)
regex_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    r"    def shutdown\(self, \*, exit_reason: str = \"menu_quit\"\) -> None:\n.*?\n    def _set_missing_credential",
    '''    def shutdown(self, *, exit_reason: str = "menu_quit") -> None:
        self.request_shutdown()
        with self._shutdown_lock:
            if self._shutdown_complete:
                return
            self._shutdown_complete = True
        with self._credential_refresh_lock:
            self._credential_refresh_generation += 1
            self._credential_refresh_in_flight = False
            self._credential_state_result = None
            self._credential_callback = None
        if self._credential_poll_timer is not None:
            self._credential_poll_timer.stop()
        if self._runtime_session_active:
            try:
                self.store.end_runtime_session(
                    self._runtime_session_id,
                    _shanghai(self._clock()).isoformat(),
                    exit_reason=exit_reason,
                    graceful_exit=True,
                )
            except Exception as error:
                self._runtime_audit_issue = f"runtime-end:{type(error).__name__}"
            self._runtime_session_active = False
        if self.capability_checks is not None:
            self.capability_checks.shutdown()
        self._universe_executor.shutdown(wait=False, cancel_futures=True)
        with self._outcome_future_lock:
            for future in self._outcome_futures:
                future.cancel()
        self._outcome_executor.shutdown(wait=False, cancel_futures=True)

    def _set_missing_credential''',
    flags=re.DOTALL,
)


# ---------------------------------------------------------------------------
# Installer/packaging hardening and alpha.5 version provenance.
# ---------------------------------------------------------------------------
replace_once(
    "src/stock_watcher/__init__.py",
    '__version__ = "0.6.0a4"',
    '__version__ = "0.6.0a5"',
)
replace_once(
    "pyproject.toml",
    'version = "0.6.0a4"',
    'version = "0.6.0a5"',
)
for relative in (
    "packaging/windows/StockWatcher.iss",
    "packaging/windows/version_info.txt",
    "scripts/windows/stockwatcher.ps1",
    "scripts/check_windows_package.py",
    ".github/workflows/governance.yml",
):
    replace_all(relative, "0.6.0-alpha.4", "0.6.0-alpha.5")
replace_all(
    "packaging/windows/version_info.txt",
    "(0, 6, 0, 1)",
    "(0, 6, 0, 5)",
    minimum=2,
)
replace_once(
    "packaging/windows/StockWatcher.iss",
    "PrivilegesRequired=lowest\n",
    "PrivilegesRequired=lowest\nCloseApplications=yes\nRestartApplications=no\n",
)
replace_once(
    "scripts/check_windows_package.py",
    '''    if "PrivilegesRequired=lowest" not in installer:
        errors.append("installer must use per-user, non-admin installation")
''',
    '''    if "PrivilegesRequired=lowest" not in installer:
        errors.append("installer must use per-user, non-admin installation")
    if "CloseApplications=yes" not in installer or "RestartApplications=no" not in installer:
        errors.append("installer upgrade must close locked app files without auto-restarting")
''',
)


# ---------------------------------------------------------------------------
# Regression tests for Windows lifecycle, DPI layout, secrets, popups, and SDK timeout.
# ---------------------------------------------------------------------------
replace_once(
    "tests/test_data_source_settings.py",
    '''    assert summary is not None and summary.maximumHeight() <= 88
    assert interrupt is not None and interrupt.maximumHeight() <= 138
    assert cards is not None and cards.minimumHeight() >= 280
''',
    '''    assert summary is not None and summary.minimumHeight() >= 120
    assert summary.maximumHeight() > 10_000
    assert interrupt is not None and interrupt.maximumHeight() > 10_000
    assert cards is not None and cards.minimumHeight() >= 180
''',
)
_write(
    "tests/test_windows_desktop_stability.py",
    '''from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QScrollArea  # noqa: E402

import stock_watcher.security.credential_store as credential_module  # noqa: E402
import stock_watcher.ui.app as app_module  # noqa: E402
from stock_watcher.config import NativeRealtimeProfile  # noqa: E402
from stock_watcher.domain import HealthState  # noqa: E402
from stock_watcher.providers.tushare.errors import (  # noqa: E402
    ProviderError,
    ProviderFailureReason,
)
from stock_watcher.providers.tushare.native_realtime_transport import (  # noqa: E402
    NativeRealtimeTransport,
)
from stock_watcher.providers.tushare.transport_protocol import TransportRequest  # noqa: E402
from stock_watcher.security import (  # noqa: E402
    PRIMARY_CREDENTIAL,
    CredentialStoreBackendError,
    KeyringCredentialStore,
    MemoryCredentialStore,
)
from stock_watcher.ui.data_source_settings import (  # noqa: E402
    DataSourceSettingsController,
    DataSourceSettingsDialog,
)
from stock_watcher.ui.data_source_status import CredentialTestResult  # noqa: E402
from stock_watcher.ui.demo import demo_batch, demo_clock  # noqa: E402
from stock_watcher.ui.history import HistoryDialog  # noqa: E402
from stock_watcher.ui.main_window import MainWindow, ReplaySession  # noqa: E402
from stock_watcher.ui.popup import AlertPopup  # noqa: E402
from stock_watcher.ui.presenter import snapshot_from_batch  # noqa: E402
from stock_watcher.ui.tushare_v1_session import TushareV1Session  # noqa: E402


def application() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def test_windows_font_preferences_do_not_force_macos_families() -> None:
    assert app_module.application_font_candidates("win32") == (
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "Segoe UI",
    )
    assert "font-family: -apple-system" not in app_module.STYLE_SHEET


def test_windows_keyring_requires_credential_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keyring_module = cast(Any, getattr(credential_module, "keyring"))
    native = type("WinVaultKeyring", (), {"__module__": "keyring.backends.Windows"})()
    monkeypatch.setattr(keyring_module, "get_keyring", lambda: native)
    store = KeyringCredentialStore(platform="win32")
    assert store.storage_label == "Windows 凭据管理器"
    assert store.backend_status().label == "Windows 凭据管理器"

    fallback = type("PlaintextBackend", (), {"__module__": "keyrings.alt.file"})()
    monkeypatch.setattr(keyring_module, "get_keyring", lambda: fallback)
    with pytest.raises(CredentialStoreBackendError, match="Windows 凭据管理器不可用"):
        store.backend_status()


def test_native_realtime_sdk_call_has_a_hard_timeout() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingClient:
        version = "test"

        def configure(self, _token: str, _verify_url: str) -> None:
            return

        def fetch(
            self,
            _codes: tuple[str, ...],
            *,
            source: str,
        ) -> list[dict[str, object]]:
            assert source == "sina"
            entered.set()
            release.wait(2.0)
            return []

    profile = NativeRealtimeProfile().model_copy(
        update={"request_timeout_seconds": 0.03}
    )
    transport = NativeRealtimeTransport(
        profile,
        lambda: "test-only-token",
        client=BlockingClient(),
    )
    request = TransportRequest(
        endpoint="tushare.realtime_quote:sina",
        api_name="realtime_quote",
        params={"ts_code": "000001.SZ"},
        realtime=True,
    )
    started = time.monotonic()
    try:
        with pytest.raises(ProviderError) as caught:
            transport.execute(request)
        assert caught.value.reason is ProviderFailureReason.TIMEOUT
        assert entered.wait(0.2)
        assert time.monotonic() - started < 0.5
    finally:
        release.set()


def test_main_window_uses_scrollable_dpi_safe_layout(tmp_path: Path) -> None:
    app = application()
    window = MainWindow(ReplaySession(tmp_path / "layout.sqlite3"))
    window.show()
    app.processEvents()
    summary = window.findChild(QLabel, "summaryValue")
    page_scroll = window.findChild(QScrollArea, "pageScroll")
    cards = window.findChild(QScrollArea, "cardsScroll")
    assert summary is not None and summary.minimumHeight() >= 38
    assert page_scroll is not None and page_scroll.widgetResizable()
    assert cards is not None and cards.minimumHeight() >= 180
    assert window.minimumWidth() <= 700
    assert window.minimumHeight() <= 420
    window.request_application_exit()
    window.close()
    app.processEvents()


def test_close_during_scan_is_nonblocking_and_cooperative(tmp_path: Path) -> None:
    app = application()
    session = ReplaySession(tmp_path / "close.sqlite3")
    calls: list[str] = []
    session.request_shutdown = lambda: calls.append("request")  # type: ignore[attr-defined]
    session.shutdown = lambda: calls.append("shutdown")  # type: ignore[attr-defined]
    window = MainWindow(session)
    window.show()
    app.processEvents()

    class FakeThread:
        running = True
        interrupted = False
        quit_called = False

        def isRunning(self) -> bool:
            return self.running

        def requestInterruption(self) -> None:
            self.interrupted = True

        def quit(self) -> None:
            self.quit_called = True

    thread = FakeThread()
    window._operation_thread = cast(Any, thread)
    event = QCloseEvent()
    started = time.monotonic()
    window.closeEvent(event)
    assert time.monotonic() - started < 0.2
    assert not event.isAccepted()
    assert calls == ["request"]
    assert thread.interrupted and thread.quit_called
    assert not window.isVisible()

    thread.running = False
    window._on_tq_thread_finished()
    app.processEvents()
    assert calls == ["request", "shutdown"]


def test_popup_clamps_complete_rectangle_and_opens_main_list(tmp_path: Path) -> None:
    app = application()
    batch = demo_batch(demo_clock())
    snapshot = snapshot_from_batch(batch, health=HealthState.HEALTHY)
    opened: list[bool] = []
    popup = AlertPopup(
        snapshot.candidates,
        "测试提醒",
        "三只观察",
        lambda _code: None,
        open_list_callback=lambda: opened.append(True),
    )
    point = popup._clamp_point(
        QRect(100, 100, 300, 200),
        QPoint(390, 290),
        width=200,
        height=100,
    )
    assert point == QPoint(200, 200)
    open_button = next(
        button for button in popup.findChildren(QPushButton) if button.text() == "打开列表"
    )
    open_button.click()
    app.processEvents()
    assert opened == [True]


def test_closing_settings_invalidates_inflight_successful_token(
    tmp_path: Path,
) -> None:
    app = application()
    entered = threading.Event()
    release = threading.Event()

    class BlockingTester:
        def test(self, _profile: object, _secret: str) -> CredentialTestResult:
            entered.set()
            release.wait(2.0)
            return CredentialTestResult(
                success=True,
                tested_at=time_to_datetime(),
                status_text="通过",
                permission_summary="通过",
                expires_at="未知",
            )

    store = MemoryCredentialStore()
    controller = DataSourceSettingsController(store=store, tester=BlockingTester())
    dialog = DataSourceSettingsDialog(controller, platform="win32")
    token = dialog.findChild(QLineEdit, "tokenInput")
    save = dialog.findChild(QPushButton, "primaryButton")
    assert token is not None and save is not None
    token.setText("temporary-test-secret")
    save.click()
    assert entered.wait(1.0)
    started = time.monotonic()
    dialog.reject()
    assert time.monotonic() - started < 0.2
    release.set()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not controller.commit_candidate("primary", confirmed=True)
    assert store.get(PRIMARY_CREDENTIAL) is None


def time_to_datetime() -> Any:
    from datetime import datetime

    return datetime.now().astimezone()


def test_history_dialog_close_does_not_wait_for_database_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = application()
    entered = threading.Event()
    release = threading.Event()

    def blocking_history(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        entered.set()
        release.wait(2.0)
        return []

    monkeypatch.setattr(
        "stock_watcher.storage.SQLiteStore.list_alert_history",
        blocking_history,
    )
    dialog = HistoryDialog(tmp_path / "history.sqlite3")
    assert entered.wait(1.0)
    started = time.monotonic()
    dialog.reject()
    assert time.monotonic() - started < 0.2
    release.set()
    app.processEvents()


def test_session_shutdown_request_cancels_scan_and_is_idempotent(tmp_path: Path) -> None:
    credentials = MemoryCredentialStore()
    credentials.set(PRIMARY_CREDENTIAL, "test-only-token")
    session = TushareV1Session(
        tmp_path / "session.sqlite3",
        credential_store=credentials,
    )

    class Runtime:
        cancellations = 0

        def request_scan_cancellation(self) -> None:
            self.cancellations += 1

    runtime = Runtime()
    session._runtime = cast(Any, runtime)
    session.request_shutdown()
    session.request_shutdown()
    assert session.shutdown_requested
    assert runtime.cancellations == 1
    session.shutdown()
    session.shutdown()
''',
)


# ---------------------------------------------------------------------------
# Changelog, version evidence, and Windows test boundary.
# ---------------------------------------------------------------------------
insert_before(
    "CHANGELOG.md",
    "## [0.6.0-alpha.4] - 2026-08-12\n",
    '''## [0.6.0-alpha.5] - 2026-08-23

### Fixed

- Windows 主窗口关闭不再在 GUI 线程等待扫描线程：点击标题栏 X 或“退出 StockWatcher”会
  立即隐藏窗口、协作取消扫描，并在线程结束后幂等释放 Session；同时为原生实时 SDK 调用
  增加硬超时，避免供应商调用无限挂起。
- Windows 使用 Microsoft YaHei UI / Microsoft YaHei / Segoe UI 的已安装字体优先级，
  修复中文回退为衬线字体；状态卡改为两行自适应布局，并通过页面级滚动适配高 DPI 和小屏。
- Token 测试、历史提醒和次日复盘读取改为非阻塞后台任务；关闭窗口会立即失效未确认 Token，
  不再同步等待 QThread。
- 提醒弹窗会按完整矩形夹紧到当前显示器工作区；“打开列表”现在真正恢复主窗口，且已关闭的
  WA_DeleteOnClose 弹窗不会留下悬空 PySide 引用。
- Windows 正式凭据存储只接受原生 Credential Manager backend，不再把任意 keyring fallback
  冒充系统安全存储。

### Packaging

- Windows 安装器候选提升为 `0.6.0-alpha.5`；覆盖安装会关闭锁定的应用文件且不会自动重启。

### Evidence boundary

- 本轮自动化与 CI 只证明离线逻辑、Windows runner 构建和回归门；DPI、多屏、标题栏关闭、
  覆盖安装、卸载和真实交易时段仍必须由目标 Windows 实机逐项验收。

''',
)
insert_before(
    "docs/visions/v0.6-candidate-outcomes/README.md",
    "## 证据边界\n",
    '''## Windows 桌面稳定性返修（0.6.0-alpha.5 候选）

本轮在不改变候选评分、StableTop3、固定提醒或次日复盘口径的前提下，集中修复 Windows
桌面生命周期和跨 DPI UI：关闭扫描中的主窗口不再阻塞 GUI；Tushare 原生实时 SDK 有硬超时；
Token 测试、历史和复盘读取可安全后台执行；Windows 中文字体、状态卡裁切、页面滚动、禁用
按钮对比度、多屏弹窗位置和“打开列表”行为均有回归覆盖。Windows Credential Manager
backend 现在按安全规则强制验证。

离线测试、Linux/macOS 回放与 Windows GitHub runner 均不能替代目标 Windows 的标题栏关闭、
100%—175% 缩放、1366×768、小屏滚动、双屏拔插、覆盖安装、卸载和真实 Token/交易时段验收。

''',
)
replace_once(
    "packaging/windows/README.md",
    "本目录保留既有 portable/Inno Setup 配置，不在本轮新增重复实现。Windows 尚未进入活跃开发；真实 M0、安装、通知、恢复和交易时段验收必须在目标 Windows 独立完成。",
    "本目录维护 portable/Inno Setup 配置与 0.6.0-alpha.5 桌面稳定性候选。Windows CI 负责离线回归和构建；真实 M0、DPI、多屏、安装、通知、关闭、恢复和交易时段验收仍必须在目标 Windows 独立完成。",
)

releases_path = _path("CURRENT_RELEASES.json")
releases = json.loads(releases_path.read_text(encoding="utf-8"))
releases["generated_at"] = "2026-08-23"
candidate = releases["candidate_outcomes"]
candidate["python_version"] = "0.6.0a5"
candidate["package_version"] = "0.6.0-alpha.5"
candidate["source_branch"] = "fix/windows-desktop-stability"
candidate["status"] = "alpha5_windows_desktop_stability_offline_candidate"
candidate["real_trading_time_validation"] = False
windows = releases["windows"]
windows["alpha5_fix_branch"] = "fix/windows-desktop-stability"
windows["alpha5_target_machine_validation"] = False
windows["alpha5_scope"] = (
    "nonblocking close and cooperative cancellation; bounded native SDK call; "
    "Windows font/DPI layout; async settings/history/outcome reads; popup geometry; "
    "native Credential Manager enforcement"
)
releases_path.write_text(
    json.dumps(releases, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
CHANGED.add("CURRENT_RELEASES.json")

print("Patched files:")
for path in sorted(CHANGED):
    print(f"- {path}")
