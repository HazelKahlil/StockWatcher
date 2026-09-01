from __future__ import annotations

import ctypes
import logging
import signal
import subprocess
import sys
from collections.abc import Callable
from time import monotonic
from typing import Any, Protocol

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Slot
from PySide6.QtGui import QAction
from PySide6.QtNetwork import QNetworkInformation
from PySide6.QtWidgets import QApplication, QMainWindow, QMenuBar

from .macos_keep_awake import (
    SETTING_AUTOSTART,
    SETTING_KEEP_AWAKE,
    TradingHoursKeepAwake,
    install_launch_agent,
    uninstall_launch_agent,
)
from .single_instance import SingleInstanceGuard as SingleInstanceGuard


class MacMainWindow(Protocol):
    def menuBar(self) -> QMenuBar: ...

    def enable_background_close(self) -> None: ...

    def request_application_exit(self) -> None: ...

    def restore_main_window(self) -> None: ...

    def begin_platform_recovery(self, reason: str) -> None: ...

    def mark_network_interrupted(self, reason: str) -> None: ...

    def _open_data_source_settings(self) -> None: ...


_logger = logging.getLogger(__name__)


class MacWindowClosePolicy:
    """State-only policy for macOS's hide-on-close application convention."""

    def __init__(self) -> None:
        self._background_close_enabled = False
        self._exit_requested = False

    def enable_background_close(self) -> None:
        self._background_close_enabled = True

    def request_application_exit(self) -> None:
        self._exit_requested = True

    @property
    def should_hide_on_close(self) -> bool:
        return self._background_close_enabled and not self._exit_requested


class NotificationCenterNotifier:
    """Best-effort macOS Notification Center delivery secondary to Qt popups."""

    def __init__(
        self,
        *,
        platform: str = sys.platform,
        runner: Callable[[list[str]], int] | None = None,
    ) -> None:
        self._platform = platform
        self._runner = runner or _run_osascript

    def notify(self, title: str, body: str) -> bool:
        if self._platform != "darwin":
            return False
        try:
            return self._runner(
                [
                    "/usr/bin/osascript",
                    "-e",
                    (
                        "on run argv\n"
                        "display notification item 2 of argv with title item 1 of argv\n"
                        "end run"
                    ),
                    title,
                    body,
                ]
            ) == 0
        except (OSError, subprocess.SubprocessError):
            # Notification permissions and user Focus mode must never affect
            # StockWatcher's in-app fixed-three-row alert.
            return False


class MacApplicationLifecycle(QObject):
    """macOS window, Dock, sleep/wake and network recovery behavior."""

    def __init__(
        self,
        app: QApplication,
        window: MacMainWindow,
        *,
        platform: str = sys.platform,
        network_information: QNetworkInformation | None = None,
        notifier: NotificationCenterNotifier | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        sleep_gap_seconds: float = 20.0,
    ) -> None:
        super().__init__(app)
        self._app = app
        self._window = window
        self._enabled = platform == "darwin"
        self._quitting = False
        self._was_suspended = False
        self._network_was_disconnected = False
        self.notifier = notifier or NotificationCenterNotifier(platform=platform)
        self._network_information = network_information
        self._monotonic_clock = monotonic_clock or monotonic
        self._sleep_gap_seconds = sleep_gap_seconds
        self._last_heartbeat = self._monotonic_clock()
        self._heartbeat = QTimer(self)
        self._keep_awake = TradingHoursKeepAwake()
        self._keep_awake_timer = QTimer(self)
        self._keep_awake_timer.setInterval(60_000)
        self._keep_awake_timer.timeout.connect(self._refresh_keep_awake)
        if not self._enabled:
            return
        self._app.setQuitOnLastWindowClosed(False)
        self._window.enable_background_close()
        self._install_application_menu()
        self._app.installEventFilter(self)
        # System-level quit (Cmd+Q, AppleEvent quit) bypasses the menu action and
        # closeEvent; record it as a graceful exit so the next launch does not
        # misreport the previous session as unclean.
        self._app.aboutToQuit.connect(self._on_about_to_quit)
        # AppleEvent quit (Dock Quit, logout, osascript) must really exit instead
        # of being swallowed by the hide-on-close window policy.
        install_apple_event_quit_handler(self._handle_external_quit)
        self._install_sigterm_fallback()
        self._app.applicationStateChanged.connect(self.handle_application_state)
        self._heartbeat.setInterval(5_000)
        self._heartbeat.timeout.connect(self.check_for_sleep_gap)
        self._heartbeat.start()
        self._keep_awake_timer.start()
        self._refresh_keep_awake()
        self._attach_network_information()

    def _refresh_keep_awake(self) -> None:
        store = getattr(getattr(self._window, "session", None), "store", None)
        enabled = False
        if store is not None:
            enabled = bool(store.get_app_setting(SETTING_KEEP_AWAKE))
        self._keep_awake.update(enabled)

    def set_keep_awake_enabled(self, enabled: bool) -> None:
        store = getattr(getattr(self._window, "session", None), "store", None)
        if store is not None:
            store.set_app_setting(SETTING_KEEP_AWAKE, bool(enabled))
        self._refresh_keep_awake()

    def show_notification(self, title: str, subtitle: str) -> bool:
        return self.notifier.notify(title, subtitle)

    def _on_about_to_quit(self) -> None:
        """Record a graceful exit when quitting via the system (Cmd+Q, AppleEvent).

        Idempotent: the menu path already ended the runtime session before this
        signal fires, so a second call is a no-op.
        """
        if not self._enabled:
            return
        self._quitting = True
        try:
            self._keep_awake.shutdown()
        except Exception:
            pass
        session = getattr(self._window, "session", None)
        shutdown = getattr(session, "shutdown", None)
        if callable(shutdown):
            shutdown(exit_reason="app_quit_event")

    def _handle_external_quit(self, exit_reason: str = "apple_event_quit") -> None:
        """Quit for real when macOS sends a quit AppleEvent or SIGTERM.

        The window close policy hides on close, which would swallow Qt's
        closeAllWindows-based quit handling; this path ends the runtime session
        first and then quits the application directly.
        """
        if not self._enabled or self._quitting:
            return
        self._quitting = True
        try:
            self._keep_awake.shutdown()
        except Exception:
            pass
        session = getattr(self._window, "session", None)
        shutdown = getattr(session, "shutdown", None)
        if callable(shutdown):
            shutdown(exit_reason=exit_reason)
        self._window.request_application_exit()
        window = self._window
        if isinstance(window, QMainWindow):
            window.close()
        self._app.quit()

    def _install_sigterm_fallback(self) -> None:
        """Record SIGTERM (kill, logout escalation) as a graceful exit when possible."""

        def _on_sigterm(_signum: int, _frame: object) -> None:
            self._handle_external_quit(exit_reason="sigterm")

        try:
            signal.signal(signal.SIGTERM, _on_sigterm)
        except (ValueError, OSError):
            pass

    def request_quit(self) -> None:
        self._quitting = True
        self._keep_awake.shutdown()
        self._window.request_application_exit()
        window = self._window
        if isinstance(window, QMainWindow):
            window.close()
        self._app.quit()

    def eventFilter(self, _watched: QObject, event: QEvent) -> bool:
        """Restore the main window for macOS reopen/activate events."""
        if self._enabled and not self._quitting and event.type() in {
            QEvent.Type.ApplicationActivate,
            QEvent.Type.FileOpen,
        }:
            QTimer.singleShot(0, self._window.restore_main_window)
        return False

    @Slot(Qt.ApplicationState)
    def handle_application_state(self, state: Qt.ApplicationState) -> None:
        if not self._enabled or self._quitting:
            return
        if state is Qt.ApplicationState.ApplicationActive:
            if self._was_suspended:
                self._mark_wake()
                self._window.begin_platform_recovery(
                    "系统已从挂起状态恢复，正在清理旧基线并重新预热数据。"
                )
            self._was_suspended = False
            self._last_heartbeat = self._monotonic_clock()
            self._window.restore_main_window()
            return
        if state is Qt.ApplicationState.ApplicationSuspended:
            self._was_suspended = True
            self._mark_sleep("系统已暂停，唤醒后将重新预热数据。")
            self._window.mark_network_interrupted(
                "系统已暂停，已停止产生新候选，唤醒后将重新预热数据。"
            )

    def _mark_sleep(self, reason: str) -> None:
        session = getattr(self._window, "session", None)
        mark_sleep = getattr(session, "mark_sleep", None)
        if callable(mark_sleep):
            mark_sleep(reason=reason)

    def _mark_wake(self) -> None:
        session = getattr(self._window, "session", None)
        mark_wake = getattr(session, "mark_wake", None)
        if callable(mark_wake):
            mark_wake(reason="系统已从挂起状态恢复")

    @Slot()
    def check_for_sleep_gap(self) -> None:
        """Detect a real event-loop pause without treating app switching as sleep."""
        if not self._enabled or self._quitting:
            return
        current = self._monotonic_clock()
        elapsed = current - self._last_heartbeat
        self._last_heartbeat = current
        if elapsed < self._sleep_gap_seconds or self._was_suspended:
            return
        self._window.begin_platform_recovery(
            "系统已从睡眠中唤醒，正在清理旧基线并重新预热数据。"
        )

    @Slot(QNetworkInformation.Reachability)
    def handle_reachability(self, reachability: QNetworkInformation.Reachability) -> None:
        if not self._enabled or self._quitting:
            return
        if reachability is QNetworkInformation.Reachability.Disconnected:
            self._network_was_disconnected = True
            self._window.mark_network_interrupted("网络连接已断开，已停止产生新候选。")
            return
        if self._network_was_disconnected and reachability in {
            QNetworkInformation.Reachability.Local,
            QNetworkInformation.Reachability.Site,
            QNetworkInformation.Reachability.Online,
        }:
            self._network_was_disconnected = False
            self._window.begin_platform_recovery("网络已恢复，正在清理旧基线并重新预热数据。")

    def _install_application_menu(self) -> None:
        menu = self._window.menuBar().addMenu("StockWatcher")
        show_window = QAction("显示主窗口", self)
        show_window.triggered.connect(self._window.restore_main_window)
        menu.addAction(show_window)
        data_source = QAction("数据接口", self)
        data_source.triggered.connect(self._window._open_data_source_settings)
        menu.addAction(data_source)
        menu.addSeparator()
        keep_awake = QAction("交易时段保持运行", self)
        keep_awake.setCheckable(True)
        store = getattr(getattr(self._window, "session", None), "store", None)
        if store is not None:
            keep_awake.setChecked(bool(store.get_app_setting(SETTING_KEEP_AWAKE)))
        keep_awake.triggered.connect(self.set_keep_awake_enabled)
        menu.addAction(keep_awake)
        auto_start = QAction("交易日09:20自动启动", self)
        auto_start.setCheckable(True)
        if store is not None:
            auto_start.setChecked(bool(store.get_app_setting(SETTING_AUTOSTART)))
        auto_start.triggered.connect(self._toggle_auto_start)
        menu.addAction(auto_start)
        menu.addSeparator()
        quit_action = QAction("退出 StockWatcher", self)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.triggered.connect(self.request_quit)
        menu.addAction(quit_action)

    def _toggle_auto_start(self, enabled: bool) -> None:
        store = getattr(getattr(self._window, "session", None), "store", None)
        if store is not None:
            store.set_app_setting(SETTING_AUTOSTART, bool(enabled))
        if enabled:
            install_launch_agent()
        else:
            uninstall_launch_agent()

    def _attach_network_information(self) -> None:
        information = self._network_information
        if information is None:
            try:
                QNetworkInformation.loadDefaultBackend()
                information = QNetworkInformation.instance()
            except RuntimeError:
                information = None
        if information is None:
            return
        self._network_information = information
        self._network_was_disconnected = (
            information.reachability()
            is QNetworkInformation.Reachability.Disconnected
        )
        if self._network_was_disconnected:
            self._window.mark_network_interrupted("网络连接已断开，已停止产生新候选。")
        information.reachabilityChanged.connect(self.handle_reachability)


def _run_osascript(arguments: list[str]) -> int:
    completed = subprocess.run(
        arguments,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
    return completed.returncode


_AE_INSTALLED = False
_ae_quit_handler_holder: list[Any] = []
_AE_CARBON_CLASS = 0x61657674  # four-char code 'aevt' (core events)
_AE_QUIT_APPLICATION = 0x71756974  # four-char code 'quit'


def install_apple_event_quit_handler(callback: Callable[[], None]) -> bool:
    """Route the macOS quit AppleEvent to a Python callback.

    Qt maps kAEQuitApplication to closeAllWindows(); our window hides instead
    of closing, so a Dock Quit / logout / osascript quit is silently swallowed
    the first time.  NSAppleEventManager handlers are consulted at the end of
    the AppleEvent dispatch chain, so registering the quit event there reliably
    consumes it before Qt's default handling runs.  The callback is marshalled
    to the Qt main thread with QTimer.singleShot so AppleEvent dispatch never
    touches Qt or database objects from a foreign thread.

    PyObjC is imported lazily; when it is unavailable the function degrades to
    a Carbon AEInstallEventHandler registration (queued behind Qt, usually
    ineffective) and reports success only so the SIGTERM fallback still gets
    installed by the caller.
    """
    global _AE_INSTALLED
    if _AE_INSTALLED:
        return True
    try:
        from Foundation import (  # type: ignore[import-not-found,import-untyped]
            NSAppleEventManager,
            NSObject,
        )
    except ImportError:
        pass
    else:
        manager = NSAppleEventManager.sharedAppleEventManager()

        class _QuitHandler(NSObject):  # type: ignore[misc]
            def handleQuitEvent_withReplyEvent_(
                self, _event: object, _reply: object
            ) -> None:
                # Marshal to the Qt main thread: quit AppleEvents are dispatched
                # on the AppleEvent manager's own thread.
                QTimer.singleShot(0, callback)

        handler = _QuitHandler.alloc().init()
        manager.setEventHandler_andSelector_forEventClass_andEventID_(
            handler,
            "handleQuitEvent:withReplyEvent:",
            int.from_bytes(b"aevt", "big"),
            int.from_bytes(b"quit", "big"),
        )
        # Keep the handler alive for the process lifetime; the AppleEvent
        # manager stores it as a weak reference.
        _ae_quit_handler_holder.append(handler)
        _AE_INSTALLED = True
        return True

    try:
        carbon = ctypes.CDLL(
            "/System/Library/Frameworks/Carbon.framework/Carbon"
        )
    except OSError:
        return False

    handler_type = ctypes.CFUNCTYPE(
        ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
    )

    @handler_type  # type: ignore[untyped-decorator]
    def _on_quit_event_carbon(
        _event: int, _reply: int, _refcon: int
    ) -> int:
        QTimer.singleShot(0, callback)
        return 0  # noErr: event consumed, do not fall through to Qt

    carbon.AEInstallEventHandler.restype = ctypes.c_int32
    carbon.AEInstallEventHandler.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_int32,  # SRefCon is a 4-byte SInt32, not a pointer
        ctypes.c_uint8,
    ]
    status = carbon.AEInstallEventHandler(
        _AE_CARBON_CLASS,
        _AE_QUIT_APPLICATION,
        ctypes.cast(_on_quit_event_carbon, ctypes.c_void_p),
        0,
        0,
    )
    if status != 0:
        return False
    _AE_INSTALLED = True
    return True
