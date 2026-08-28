from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from time import monotonic
from typing import Any, Protocol, cast

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_logger = logging.getLogger(__name__)


class _AckTarget(Protocol):
    """Minimal socket surface used to deliver an activation ACK.

    Kept structural so tests can inject a fake that raises the shiboken
    RuntimeError without creating a real (and already-deleted) QLocalSocket.
    """

    def isValid(self) -> bool: ...

    def write(self, data: bytes) -> int: ...

    def flush(self) -> bool: ...

    def waitForBytesWritten(self, msecs: int) -> bool: ...

    def disconnectFromServer(self) -> None: ...


class SingleInstanceGuard(QObject):
    """Acknowledged, metadata-carrying activation protocol for desktop apps."""

    activation_requested = Signal()

    def __init__(
        self,
        name: str | None = None,
        parent: QObject | None = None,
        *,
        app_path: str | None = None,
        source_commit: str | None = None,
        activation_handler: Callable[[dict[str, object]], Mapping[str, object]] | None = None,
        ack_timeout_ms: int = 4_000,
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

    def activate_existing(self) -> bool:
        """Ask a running primary to restore its window without becoming primary."""
        self.last_role = "secondary"
        return self._request_activation()

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
        # The secondary may have disconnected and its C++ object been deleted
        # before the readyRead callback runs; touching it raises a shiboken
        # RuntimeError that must not escape the Qt callback.
        try:
            if not socket.isValid():
                self._buffers.pop(id(socket), None)
                return
            incoming = bytes(cast(Any, socket.readAll()))
        except RuntimeError:
            self._buffers.pop(id(socket), None)
            return
        key = id(socket)
        self._buffers[key] = self._buffers.get(key, b"") + incoming
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

    def _send_ack(self, socket: _AckTarget, response: Mapping[str, object]) -> None:
        """Write the ACK without letting a torn-down socket crash the app."""
        try:
            if not socket.isValid():
                self._log_ack_failure(response, "socket-invalid")
                return
            socket.write(_json_line(dict(response)))
            socket.flush()
            socket.waitForBytesWritten(500)
            socket.disconnectFromServer()
        except RuntimeError as error:  # libshiboken: C++ object already deleted
            self._log_ack_failure(response, f"{type(error).__name__}: {error}")

    def _log_ack_failure(self, response: Mapping[str, object], reason: str) -> None:
        """Diagnostics for a failed activation ACK; never touches the socket."""
        _logger.warning(
            "activation ACK not delivered: reason=%s result=%s primary_pid=%s "
            "primary_app_path=%s primary_source_commit=%s",
            reason,
            response.get("result"),
            os.getpid(),
            self.app_path,
            self.source_commit,
        )

    def _discard_connection(self, socket: QLocalSocket) -> None:
        self._buffers.pop(id(socket), None)
        if socket in self._connections:
            self._connections.remove(socket)
        try:
            socket.deleteLater()
        except RuntimeError:
            pass


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
    if getattr(sys, "frozen", False):
        return str(executable)
    return str(Path(sys.argv[0]).resolve())


def _timestamp() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat()


def _default_instance_name() -> str:
    if hasattr(os, "getuid"):
        identifier = f"StockWatcher:{os.getuid()}"
    else:
        identifier = f"StockWatcher:{os.environ.get('USERNAME', os.environ.get('USER', 'user'))}"
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:16]
    return f"stockwatcher-{digest}"
