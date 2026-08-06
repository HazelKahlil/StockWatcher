from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from time import monotonic
from typing import Any, Protocol, cast

from PySide6.QtCore import QEvent, QEventLoop, QObject, Qt, QTimer, Signal, Slot
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
    """Mac-local guard with an acknowledged, metadata-carrying activation protocol."""

    activation_requested = Signal()

    def __init__(
        self,
        name: str | None = None,
        parent: QObject | None = None,
        *,
        app_path: str | None = None,
        source_commit: str | None = None,
        activation_handler: Callable[[dict[str, object]], Mapping[str, object]] | None = None,
        ack_timeout_ms: int = 1_500,
    ) -> None:
        super().__init__(parent)
        self.name = name or _default_instance_name()
        self.app_path = app_path or _current_application_path()
        self.source_commit = source_commit or "unknown"
        self.ack_timeout_ms = max(100, ack_timeout_ms)
        self._activation_handler = activation_handler
        self._server = QLocalServer(self)
        self._connections: list[QLocalSocket] = []
        self._buffers: dict[int, bytes] = {}
        self._primary = False
        self._last_socket_error: QLocalSocket.LocalSocketError | None = None
        self.last_role = "unstarted"
        self.last_activation_status = "not-attempted"
        self.last_activation_ack: dict[str, object] = {}

    @property
    def is_primary(self) -> bool:
        return self._primary

    @property
    def is_secondary(self) -> bool:
        return self.last_role == "secondary"

    def set_activation_handler(
        self,
        handler: Callable[[dict[str, object]], Mapping[str, object]],
    ) -> None:
        self._activation_handler = handler

    def acquire(self) -> bool:
        """Become primary, or request activation from an already-running app."""
        self.last_role = "checking"
        if self._server.listen(self.name):
            self._become_primary()
            return True
        if self._request_activation():
            self.last_role = "secondary"
            return False
        self.last_role = "secondary"
        # A connected endpoint that did not acknowledge activation is not stale.
        # Never remove it just because a hidden primary failed to answer quickly.
        if self.last_activation_status in {"no-ack", "version-conflict", "activation-failed"}:
            return False
        # Only clear a socket path when no server exists.  A live primary must
        # never be removed merely because it was temporarily busy.
        if self._socket_error_allows_stale_recovery():
            QLocalServer.removeServer(self.name)
            if self._server.listen(self.name):
                self._become_primary()
                return True
        self.last_activation_status = "single-instance-failed"
        raise RuntimeError("无法建立 StockWatcher 单实例通信通道")

    def close(self) -> None:
        if not self._primary:
            return
        self._server.close()
        QLocalServer.removeServer(self.name)
        self._primary = False

    def _become_primary(self) -> None:
        self._primary = True
        self.last_role = "primary"
        self.last_activation_status = "primary"
        self._server.newConnection.connect(self._accept_connections)

    def _request_activation(self) -> bool:
        socket = QLocalSocket(self)
        socket.connectToServer(self.name)
        if not socket.waitForConnected(500):
            self._last_socket_error = socket.error()
            self.last_activation_status = "connection-failed"
            socket.deleteLater()
            return False
        request = {
            "command": "activate",
            "secondary_pid": os.getpid(),
            "secondary_app_path": self.app_path,
            "secondary_source_commit": self.source_commit,
            "timestamp": _timestamp(),
        }
        socket.write(_json_line(request))
        socket.flush()
        socket.waitForBytesWritten(500)
        deadline = monotonic() + (self.ack_timeout_ms / 1000.0)
        response_bytes = b""
        while monotonic() < deadline:
            if socket.bytesAvailable() > 0:
                response_bytes = bytes(cast(Any, socket.readAll()))
                break
            loop = QEventLoop()
            QTimer.singleShot(20, loop.quit)
            loop.exec()
            if socket.bytesAvailable() > 0:
                response_bytes = bytes(cast(Any, socket.readAll()))
                break
        if not response_bytes:
            self.last_activation_status = "no-ack"
            socket.disconnectFromServer()
            socket.deleteLater()
            return False
        response = _decode_line(response_bytes)
        self.last_activation_ack = response
        result = str(response.get("result", ""))
        self.last_activation_status = result or "activation-failed"
        socket.disconnectFromServer()
        socket.deleteLater()
        return result == "success"

    def _socket_error_allows_stale_recovery(self) -> bool:
        """Allow recovery only when the local endpoint is demonstrably dead."""
        return self._last_socket_error in {
            QLocalSocket.LocalSocketError.ServerNotFoundError,
            QLocalSocket.LocalSocketError.ConnectionRefusedError,
        }

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
        key = id(socket)
        self._buffers[key] = self._buffers.get(key, b"") + bytes(cast(Any, socket.readAll()))
        if b"\n" not in self._buffers[key]:
            return
        raw, _separator, remainder = self._buffers.pop(key).partition(b"\n")
        if not raw:
            return
        request = _decode_line(raw)
        if request.get("command") != "activate":
            self._send_ack(socket, {"result": "invalid-command"})
            return
        self.activation_requested.emit()
        conflict = _metadata_conflicts(request, self.app_path, self.source_commit)
        response: dict[str, object] = {
            "ok": True,
            "result": "success",
            "window_visible": True,
            "window_minimized": False,
            "application_active": True,
            "activation_timestamp": _timestamp(),
            "error_reason": None,
        }
        if conflict:
            # Restore the known primary so the user can choose to keep using it,
            # but never report a different build as a successful activation.
            if self._activation_handler is not None:
                try:
                    response.update(dict(self._activation_handler(request)))
                except Exception as error:
                    response.update(
                        {
                            "ok": False,
                            "result": "version-conflict",
                            "window_visible": False,
                            "window_minimized": False,
                            "application_active": False,
                            "error_reason": type(error).__name__,
                        }
                    )
            response["ok"] = False
            response["result"] = "version-conflict"
            response["error_reason"] = response.get("error_reason") or (
                "App路径或SOURCE_COMMIT不一致"
            )
        elif self._activation_handler is not None:
            try:
                response.update(dict(self._activation_handler(request)))
            except Exception as error:
                response.update(
                    {
                        "ok": False,
                        "result": "activation-failed",
                        "window_visible": False,
                        "window_minimized": False,
                        "application_active": False,
                        "error_reason": type(error).__name__,
                    }
                )
        response.update(
            {
                "primary_pid": os.getpid(),
                "primary_app_path": self.app_path,
                "primary_source_commit": self.source_commit,
                "activation_timestamp": response.get("activation_timestamp") or _timestamp(),
            }
        )
        response["ok"] = bool(response.get("ok", response.get("result") == "success"))
        response["window_visible"] = bool(response.get("window_visible", False))
        response["window_minimized"] = bool(response.get("window_minimized", False))
        response["application_active"] = bool(response.get("application_active", False))
        response["error_reason"] = response.get("error_reason")
        self._send_ack(socket, response)

    def _send_ack(self, socket: QLocalSocket, response: Mapping[str, object]) -> None:
        socket.write(_json_line(dict(response)))
        socket.flush()
        socket.waitForBytesWritten(500)
        socket.disconnectFromServer()

    def _discard_connection(self, socket: QLocalSocket) -> None:
        self._buffers.pop(id(socket), None)
        if socket in self._connections:
            self._connections.remove(socket)
        socket.deleteLater()


def _json_line(value: Mapping[str, object]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _decode_line(value: bytes) -> dict[str, object]:
    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"command": "activate"} if value.strip() == b"activate" else {}
    return parsed if isinstance(parsed, dict) else {}


def _metadata_conflicts(
    request: Mapping[str, object],
    primary_path: str,
    primary_commit: str,
) -> bool:
    requested_path = str(request.get("secondary_app_path", ""))
    requested_commit = str(request.get("secondary_source_commit", ""))
    return bool(
        requested_path
        and primary_path
        and requested_path != primary_path
        or requested_commit
        and primary_commit
        and requested_commit != primary_commit
    )


def _current_application_path() -> str:
    executable = Path(sys.executable).resolve()
    for parent in (executable, *executable.parents):
        if parent.suffix == ".app":
            return str(parent)
    return str(Path(sys.argv[0]).resolve())


def _timestamp() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()

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
        if not self._enabled:
            return
        self._app.setQuitOnLastWindowClosed(False)
        self._window.enable_background_close()
        self._install_application_menu()
        self._app.installEventFilter(self)
        self._app.applicationStateChanged.connect(self.handle_application_state)
        self._heartbeat.setInterval(5_000)
        self._heartbeat.timeout.connect(self.check_for_sleep_gap)
        self._heartbeat.start()
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
                self._window.begin_platform_recovery(
                    "系统已从挂起状态恢复，正在清理旧基线并重新预热数据。"
                )
            self._was_suspended = False
            self._last_heartbeat = self._monotonic_clock()
            self._window.restore_main_window()
            return
        if state is Qt.ApplicationState.ApplicationSuspended:
            self._was_suspended = True
            self._window.mark_network_interrupted(
                "系统已暂停，已停止产生新候选，唤醒后将重新预热数据。"
            )

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
