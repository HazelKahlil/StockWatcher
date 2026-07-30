from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtNetwork import QLocalServer, QLocalSocket, QNetworkInformation
from PySide6.QtWidgets import QApplication, QMainWindow, QMenuBar


class MacMainWindow(Protocol):
    def menuBar(self) -> QMenuBar: ...

    def enable_background_close(self) -> None: ...

    def request_application_exit(self) -> None: ...

    def restore_main_window(self) -> None: ...

    def begin_platform_recovery(self, reason: str) -> None: ...

    def mark_network_interrupted(self, reason: str) -> None: ...

    def _open_data_source_settings(self) -> None: ...


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


class SingleInstanceGuard(QObject):
    """Mac-local Qt socket guard that wakes, rather than duplicates, the app."""

    activation_requested = Signal()

    def __init__(self, name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.name = name or _default_instance_name()
        self._server = QLocalServer(self)
        self._connections: list[QLocalSocket] = []
        self._primary = False

    @property
    def is_primary(self) -> bool:
        return self._primary

    def acquire(self) -> bool:
        """Become primary, or request activation from an already-running app."""
        if self._server.listen(self.name):
            self._primary = True
            self._server.newConnection.connect(self._accept_connections)
            return True
        if self._request_activation():
            return False
        # Only clear a socket path when no server exists.  A live primary must
        # never be removed merely because it was temporarily busy.
        if self._socket_error_is_server_missing():
            QLocalServer.removeServer(self.name)
            if self._server.listen(self.name):
                self._primary = True
                self._server.newConnection.connect(self._accept_connections)
                return True
        raise RuntimeError("无法建立 StockWatcher 单实例通信通道")

    def close(self) -> None:
        if not self._primary:
            return
        self._server.close()
        QLocalServer.removeServer(self.name)
        self._primary = False

    def _request_activation(self) -> bool:
        socket = QLocalSocket(self)
        socket.connectToServer(self.name)
        if not socket.waitForConnected(500):
            self._last_socket_error = socket.error()
            socket.deleteLater()
            return False
        socket.write(b"activate\n")
        socket.flush()
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()
        socket.deleteLater()
        return True

    def _socket_error_is_server_missing(self) -> bool:
        error = getattr(self, "_last_socket_error", None)
        return error == QLocalSocket.LocalSocketError.ServerNotFoundError

    @Slot()
    def _accept_connections(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            self._connections.append(socket)
            socket.readyRead.connect(lambda socket=socket: self._read_activation(socket))
            socket.disconnected.connect(
                lambda socket=socket: self._discard_connection(socket)
            )
            self._read_activation(socket)

    def _read_activation(self, socket: QLocalSocket) -> None:
        if socket.readAll().isEmpty():
            return
        self.activation_requested.emit()
        socket.disconnectFromServer()

    def _discard_connection(self, socket: QLocalSocket) -> None:
        if socket in self._connections:
            self._connections.remove(socket)
        socket.deleteLater()


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
    ) -> None:
        super().__init__(app)
        self._app = app
        self._window = window
        self._enabled = platform == "darwin"
        self._quitting = False
        self._was_inactive = False
        self._network_was_disconnected = False
        self.notifier = notifier or NotificationCenterNotifier(platform=platform)
        self._network_information = network_information
        if not self._enabled:
            return
        self._app.setQuitOnLastWindowClosed(False)
        self._window.enable_background_close()
        self._install_application_menu()
        self._app.applicationStateChanged.connect(self.handle_application_state)
        self._attach_network_information()

    def show_notification(self, title: str, subtitle: str) -> bool:
        return self.notifier.notify(title, subtitle)

    def request_quit(self) -> None:
        self._quitting = True
        self._window.request_application_exit()
        window = self._window
        if isinstance(window, QMainWindow):
            window.close()
        self._app.quit()

    @Slot(Qt.ApplicationState)
    def handle_application_state(self, state: Qt.ApplicationState) -> None:
        if not self._enabled or self._quitting:
            return
        if state is Qt.ApplicationState.ApplicationActive:
            if self._was_inactive:
                self._window.begin_platform_recovery("系统唤醒或回到前台，正在重新预热数据。")
            self._was_inactive = False
            self._window.restore_main_window()
            return
        if state in {
            Qt.ApplicationState.ApplicationInactive,
            Qt.ApplicationState.ApplicationSuspended,
        }:
            self._was_inactive = True
            if state is Qt.ApplicationState.ApplicationSuspended:
                self._window.mark_network_interrupted(
                    "系统已暂停，已停止产生新候选，唤醒后将重新预热数据。"
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
        quit_action = QAction("退出 StockWatcher", self)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)
        quit_action.triggered.connect(self.request_quit)
        menu.addAction(quit_action)

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


def _default_instance_name() -> str:
    identifier = f"StockWatcher:{getattr(os, 'getuid', lambda: 0)()}"
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:16]
    return f"stockwatcher-{digest}"


def _run_osascript(arguments: list[str]) -> int:
    completed = subprocess.run(
        arguments,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
    )
    return completed.returncode
