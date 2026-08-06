from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtNetwork import QLocalServer, QNetworkInformation  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMenuBar  # noqa: E402

import stock_watcher.paths as paths_module  # noqa: E402
import stock_watcher.security.credential_store as credential_module  # noqa: E402
import stock_watcher.ui.app as app_module  # noqa: E402
import stock_watcher.ui.main_window as main_window_module  # noqa: E402
from stock_watcher.domain import HealthState, Security  # noqa: E402
from stock_watcher.providers.tushare.models import TransportResult  # noqa: E402
from stock_watcher.providers.tushare.transport_protocol import TransportRequest  # noqa: E402
from stock_watcher.runtime import (  # noqa: E402
    FullMarketScanCoordinator,
    ScanCancelledError,
    TushareV1Runtime,
)
from stock_watcher.runtime.scan_coordinator import RealtimeTransport  # noqa: E402
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
from stock_watcher.ui.macos import (  # noqa: E402
    MacApplicationLifecycle,
    MacWindowClosePolicy,
    NotificationCenterNotifier,
    SingleInstanceGuard,
    install_apple_event_quit_handler,
)
from stock_watcher.ui.main_window import MainWindow, ReplaySession  # noqa: E402
from stock_watcher.ui.tushare_v1_session import TushareV1Session  # noqa: E402


def application() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def test_macos_application_uses_padded_icon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

    icon_path = app_module.application_icon_path()

    assert icon_path.name == "stockwatcher-macos.png"
    assert icon_path.is_file()


def test_non_macos_application_keeps_windows_icon_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    assert app_module.application_icon_path().name == "stockwatcher.png"


def test_macos_runtime_paths_split_application_support_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))

    paths = paths_module.runtime_paths()
    paths.create()

    assert paths.root == tmp_path / "Library" / "Application Support" / "StockWatcher"
    assert paths.database == paths.root / "data" / "stock-watcher.sqlite3"
    assert paths.logs == tmp_path / "Library" / "Logs" / "StockWatcher"
    assert paths.data.is_dir()
    assert paths.logs.is_dir()
    assert paths.reports.is_dir()
    assert paths_module.report_directory_for_database(paths.database) == paths.reports
    explicit = tmp_path / "test.sqlite3"
    assert paths_module.report_directory_for_database(explicit) == tmp_path / "reports"


def test_macos_keyring_store_requires_native_keychain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable_backend = type("FallbackBackend", (), {"__module__": "keyring.backends.fail"})()
    keyring_module = cast(Any, getattr(credential_module, "keyring"))
    monkeypatch.setattr(keyring_module, "get_keyring", lambda: unavailable_backend)

    with pytest.raises(CredentialStoreBackendError, match="系统钥匙串不可用"):
        KeyringCredentialStore(platform="darwin").backend_status()


def test_macos_keyring_store_reports_native_keychain_and_ui_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application()
    native_backend = type(
        "Keyring",
        (),
        {"__module__": "keyring.backends.macOS"},
    )()
    keyring_module = cast(Any, getattr(credential_module, "keyring"))
    monkeypatch.setattr(keyring_module, "get_keyring", lambda: native_backend)
    store = KeyringCredentialStore(platform="darwin")

    assert store.backend_status().label == "系统钥匙串"

    class MacMemoryStore(MemoryCredentialStore):
        @property
        def storage_label(self) -> str:
            return "系统钥匙串"

        def backend_status(self) -> object:
            return object()

    dialog = DataSourceSettingsDialog(
        DataSourceSettingsController(store=MacMemoryStore())
    )
    copy = " ".join(label.text() for label in dialog.findChildren(QLabel))
    placeholders = [field.placeholderText() for field in dialog.findChildren(QLineEdit)]
    assert "系统钥匙串" in copy
    assert any("系统钥匙串" in value for value in placeholders)
    assert store.storage_label == "系统钥匙串"
    dialog.close()


def test_single_instance_guard_wakes_existing_window() -> None:
    app = application()
    name = f"stockwatcher-test-{uuid.uuid4().hex}"
    primary = SingleInstanceGuard(
        name,
        app_path="/primary/StockWatcher.app",
        source_commit="commit-a",
    )
    secondary = SingleInstanceGuard(
        name,
        app_path="/primary/StockWatcher.app",
        source_commit="commit-a",
    )
    activated: list[dict[str, object]] = []
    primary.activation_requested.connect(lambda: activated.append({"signal": True}))
    def handler(request: dict[str, object]) -> dict[str, object]:
        activated.append(request)
        return {"window_visible": True, "result": "success"}

    primary.set_activation_handler(handler)

    try:
        assert primary.acquire()
        assert not secondary.acquire()
        deadline = time.monotonic() + 1.0
        while len(activated) < 2 and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert activated[0] == {"signal": True}
        request = activated[1]
        assert request["command"] == "activate"
        assert request["secondary_pid"] == os.getpid()
        assert request["secondary_app_path"] == "/primary/StockWatcher.app"
        assert request["secondary_source_commit"] == "commit-a"
        assert secondary.last_activation_status == "success"
        assert secondary.last_activation_ack["primary_app_path"] == "/primary/StockWatcher.app"
        assert secondary.last_activation_ack["primary_source_commit"] == "commit-a"
    finally:
        secondary.close()
        primary.close()


def test_single_instance_guard_does_not_silently_exit_without_ack() -> None:
    application()
    name = f"stockwatcher-no-ack-{uuid.uuid4().hex}"
    primary = SingleInstanceGuard(name, ack_timeout_ms=100)
    secondary = SingleInstanceGuard(name, ack_timeout_ms=100)

    try:
        assert primary.acquire()
        # The primary accepts a connection but intentionally never processes it.
        primary._server.newConnection.disconnect(primary._accept_connections)
        assert not secondary.acquire()
        assert secondary.last_activation_status == "no-ack"
        assert not secondary.is_primary
    finally:
        secondary.close()
        primary.close()


def test_single_instance_guard_reports_version_conflict_without_replacing_primary() -> None:
    application()
    name = f"stockwatcher-conflict-{uuid.uuid4().hex}"
    primary = SingleInstanceGuard(name, app_path="/old/StockWatcher.app", source_commit="old")
    secondary = SingleInstanceGuard(name, app_path="/new/StockWatcher.app", source_commit="new")
    restored: list[dict[str, object]] = []

    def handler(request: dict[str, object]) -> dict[str, object]:
        restored.append(request)
        return {"window_visible": True}

    primary.set_activation_handler(handler)

    try:
        assert primary.acquire()
        assert not secondary.acquire()
        assert secondary.last_activation_status == "version-conflict"
        assert secondary.last_activation_ack["primary_app_path"] == "/old/StockWatcher.app"
        assert primary.is_primary
        assert restored and restored[0]["command"] == "activate"
    finally:
        secondary.close()
        primary.close()


def test_single_instance_guard_recovers_after_interrupted_primary() -> None:
    """A stale Unix socket must not make the next app launch permanently fail."""
    application()
    name = f"stockwatcher-stale-{uuid.uuid4().hex}"
    interrupted = QLocalServer()
    recovered = SingleInstanceGuard(name)

    try:
        assert interrupted.listen(name)
        # Closing without removeServer mirrors an interrupted process: no
        # primary is accepting connections, but the socket pathname remains.
        interrupted.close()
        assert recovered.acquire()
        assert recovered.is_primary
    finally:
        recovered.close()
        QLocalServer.removeServer(name)


def test_worker_exit_precedes_ui_refresh_and_queued_manual_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signal order avoids the Qt/GIL deadlock and preserves manual intent."""

    class FakeSignal:
        def __init__(self) -> None:
            self.callbacks: list[Any] = []

        def connect(self, callback: Any) -> None:
            self.callbacks.append(callback)

        def emit(self) -> None:
            for callback in tuple(self.callbacks):
                callback()

    fake_threads: list[FakeThread] = []
    fake_workers: list[FakeWorker] = []

    class FakeThread:
        def __init__(self, _parent: object) -> None:
            self.started = FakeSignal()
            self.finished = FakeSignal()
            self.running = False
            self.deleted = False
            fake_threads.append(self)

        def start(self) -> None:
            self.running = True

        def quit(self) -> None:
            self.running = False

        def isRunning(self) -> bool:
            return self.running

        def deleteLater(self) -> None:
            self.deleted = True

    class FakeWorker:
        def __init__(self, _session: object, _operation: str) -> None:
            self.finished = FakeSignal()
            self.deleted = False
            fake_workers.append(self)

        def moveToThread(self, _thread: object) -> None:
            return

        def run(self) -> None:
            return

        def deleteLater(self) -> None:
            self.deleted = True

    application()
    monkeypatch.setattr(main_window_module, "QThread", FakeThread)
    monkeypatch.setattr(main_window_module, "_SessionOperationWorker", FakeWorker)
    session = ReplaySession(tmp_path / "thread-order.sqlite3")
    session.supports_manual_fetch = True
    window = MainWindow(session)
    session.is_replay = False

    try:
        window._start_tq_operation("check")
        check_thread = fake_threads[-1]
        check_worker = fake_workers[-1]

        assert window._on_tq_operation_finished not in check_worker.finished.callbacks
        assert check_thread.finished.callbacks[0] == window._on_tq_operation_finished

        window._manual_fetch_tq()
        assert window._queued_manual_fetch
        assert window._manual_fetch_action.isEnabled()
        assert "已排队" in window._manual_fetch_action.text()

        check_worker.finished.emit()
        assert window._active_operation == "check"
        check_thread.finished.emit()

        assert check_worker.deleted
        assert check_thread.deleted
        assert not window._queued_manual_fetch
        assert window._active_operation == "fetch"

        fetch_thread = fake_threads[-1]
        fetch_worker = fake_workers[-1]
        fetch_worker.finished.emit()
        fetch_thread.finished.emit()

        assert fetch_worker.deleted
        assert fetch_thread.deleted
        assert window._operation_thread is None
        assert window._operation_worker is None
        assert window._active_operation is None
        assert not window._operation_progress_timer.isActive()
        assert window._manual_fetch_action.isEnabled()
    finally:
        window.request_application_exit()
        window.close()


def test_macos_close_policy_hides_only_until_explicit_exit() -> None:
    policy = MacWindowClosePolicy()
    assert not policy.should_hide_on_close

    policy.enable_background_close()
    assert policy.should_hide_on_close

    policy.request_application_exit()
    assert not policy.should_hide_on_close


@dataclass
class _SignalRecorder:
    callback: object | None = None

    def connect(self, callback: object) -> None:
        self.callback = callback


@dataclass
class _FakeNetworkInformation:
    reachabilityChanged: _SignalRecorder = field(default_factory=_SignalRecorder)

    def reachability(self) -> QNetworkInformation.Reachability:
        return QNetworkInformation.Reachability.Online


class _FakeMenu:
    def addAction(self, _action: object) -> None:
        return

    def addSeparator(self) -> None:
        return


class _FakeMenuBar:
    def __init__(self) -> None:
        self.titles: list[str] = []

    def addMenu(self, title: str) -> _FakeMenu:
        self.titles.append(title)
        return _FakeMenu()


class _LifecycleWindow:
    def __init__(self) -> None:
        self.background_close_enabled = False
        self.restore_calls = 0
        self.recovery_reasons: list[str] = []
        self.network_reasons: list[str] = []
        self.menu_bar = _FakeMenuBar()
        self.session: object | None = None

    def menuBar(self) -> QMenuBar:
        return cast(QMenuBar, self.menu_bar)

    def enable_background_close(self) -> None:
        self.background_close_enabled = True

    def request_application_exit(self) -> None:
        return

    def restore_main_window(self) -> None:
        self.restore_calls += 1

    def begin_platform_recovery(self, reason: str) -> None:
        self.recovery_reasons.append(reason)

    def mark_network_interrupted(self, reason: str) -> None:
        self.network_reasons.append(reason)

    def _open_data_source_settings(self) -> None:
        return


def test_macos_lifecycle_ignores_focus_changes_but_warms_after_suspend() -> None:
    app = application()
    window = _LifecycleWindow()
    lifecycle = MacApplicationLifecycle(
        app,
        cast(Any, window),
        platform="darwin",
        network_information=cast(QNetworkInformation, _FakeNetworkInformation()),
    )

    lifecycle.handle_application_state(Qt.ApplicationState.ApplicationInactive)
    lifecycle.handle_application_state(Qt.ApplicationState.ApplicationActive)
    assert window.recovery_reasons == []

    lifecycle.handle_application_state(Qt.ApplicationState.ApplicationSuspended)
    lifecycle.handle_application_state(Qt.ApplicationState.ApplicationActive)
    lifecycle.handle_reachability(QNetworkInformation.Reachability.Disconnected)
    lifecycle.handle_reachability(QNetworkInformation.Reachability.Online)

    assert window.background_close_enabled
    assert window.restore_calls >= 1
    assert any("挂起状态恢复" in reason for reason in window.recovery_reasons)
    assert window.network_reasons[0] == (
        "系统已暂停，已停止产生新候选，唤醒后将重新预热数据。"
    )
    assert window.network_reasons[1] == "网络连接已断开，已停止产生新候选。"
    assert any("网络已恢复" in reason for reason in window.recovery_reasons)
    assert window.menu_bar.titles == ["StockWatcher"]


def test_macos_lifecycle_detects_sleep_gap_without_focus_false_positive() -> None:
    app = application()
    window = _LifecycleWindow()
    clock = [100.0]
    lifecycle = MacApplicationLifecycle(
        app,
        cast(Any, window),
        platform="darwin",
        network_information=cast(QNetworkInformation, _FakeNetworkInformation()),
        monotonic_clock=lambda: clock[0],
        sleep_gap_seconds=20.0,
    )

    clock[0] = 105.0
    lifecycle.check_for_sleep_gap()
    lifecycle.handle_application_state(Qt.ApplicationState.ApplicationInactive)
    lifecycle.handle_application_state(Qt.ApplicationState.ApplicationActive)
    assert window.recovery_reasons == []

    clock[0] = 130.0
    lifecycle.check_for_sleep_gap()

    assert window.recovery_reasons == [
        "系统已从睡眠中唤醒，正在清理旧基线并重新预热数据。"
    ]
    assert window.network_reasons == []
    assert window.menu_bar.titles == ["StockWatcher"]


def test_notification_center_is_secondary_and_permission_failures_are_safe() -> None:
    calls: list[list[str]] = []

    def rejected_notification(arguments: list[str]) -> int:
        calls.append(arguments)
        return 1

    notifier = NotificationCenterNotifier(
        platform="darwin",
        runner=rejected_notification,
    )

    assert not notifier.notify("本轮观察提醒", "三只候选")
    assert calls and calls[0][0] == "/usr/bin/osascript"


@dataclass
class _RecoverableRuntime:
    cancellation_requests: int = 0
    resets: int = 0

    def request_scan_cancellation(self) -> None:
        self.cancellation_requests += 1

    def reset_for_external_recovery(self) -> None:
        self.resets += 1


def test_platform_recovery_clears_old_baseline_and_keeps_previous_results(tmp_path: Path) -> None:
    credentials = MemoryCredentialStore()
    credentials.set(PRIMARY_CREDENTIAL, "test-only-token")
    session = TushareV1Session(tmp_path / "session.sqlite3", credential_store=credentials)
    runtime = _RecoverableRuntime()
    session._runtime = cast(TushareV1Runtime, runtime)
    session._prepared_date = date(2026, 7, 30)

    session.begin_platform_recovery("系统唤醒，正在重新预热数据。")

    assert runtime.cancellation_requests == 1
    assert session.state is HealthState.WARMING
    assert session.candidate_gate_label == "暂停新候选"
    assert session._apply_pending_platform_recovery()
    assert runtime.resets == 1
    assert session._prepared_date == date(2026, 7, 30)
    assert "连续3轮" in session.status_issues[0]

    session.mark_network_interrupted("网络连接已断开，已停止产生新候选。")
    assert runtime.cancellation_requests == 2
    assert cast(HealthState, session.state) is HealthState.STOPPED
    assert session.candidate_gate_label == "无新结果"
    session.shutdown()


def test_network_interruption_blocks_scheduled_scan_until_recovery(tmp_path: Path) -> None:
    credentials = MemoryCredentialStore()
    credentials.set(PRIMARY_CREDENTIAL, "test-only-token")
    calls: list[str] = []

    def runtime_factory(*_args: object) -> tuple[TushareV1Runtime, object]:
        calls.append("runtime")
        raise AssertionError("network interruption must prevent a new runtime scan")

    session = TushareV1Session(
        tmp_path / "session.sqlite3",
        credential_store=credentials,
        runtime_factory=cast(Any, runtime_factory),
    )
    session.mark_network_interrupted("网络连接已断开，已停止产生新候选。")

    session.recover()

    assert calls == []
    assert session.state is HealthState.STOPPED
    assert session.candidate_gate_label == "无新结果"
    session.shutdown()


def test_macos_first_launch_without_token_requests_simple_setup(tmp_path: Path) -> None:
    credentials = MemoryCredentialStore()
    session = TushareV1Session(tmp_path / "session.sqlite3", credential_store=credentials)

    assert session.requires_data_source_setup

    credentials.set(PRIMARY_CREDENTIAL, "test-only-token")
    assert not session.requires_data_source_setup
    session.shutdown()


def test_default_macos_entrypoint_keeps_tdxquant_out_of_the_process() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import stock_watcher.ui.app; "
                "assert not any(name.startswith('stock_watcher.providers.tdx') "
                "or name == 'stock_watcher.ui.tdx_session' for name in sys.modules)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_cancelled_scan_response_cannot_form_a_market_snapshot() -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingTransport:
        def execute(self, _request: TransportRequest) -> TransportResult:
            started.set()
            assert release.wait(1.0)
            return cast(TransportResult, object())

    coordinator = FullMarketScanCoordinator(cast(RealtimeTransport, BlockingTransport()))
    failure: list[Exception] = []

    def run_scan() -> None:
        try:
            coordinator.fetch_once((Security("000001.SZ", "平安银行", "SZ"),))
        except Exception as error:
            failure.append(error)

    thread = threading.Thread(target=run_scan)
    thread.start()
    assert started.wait(1.0)
    coordinator.cancel_current_scan()
    release.set()
    thread.join(timeout=1.0)

    assert len(failure) == 1
    assert isinstance(failure[0], ScanCancelledError)


def test_macos_lifecycle_records_graceful_quit_on_system_quit_event() -> None:
    """Cmd+Q / AppleEvent quit must end the runtime session gracefully."""
    app = application()
    window = _LifecycleWindow()
    calls: list[tuple[str, str]] = []

    class _FakeSession:
        def shutdown(self, *, exit_reason: str = "menu_quit") -> None:
            calls.append(("shutdown", exit_reason))

    window.session = _FakeSession()
    lifecycle = MacApplicationLifecycle(
        app,
        cast(Any, window),
        platform="darwin",
        network_information=cast(QNetworkInformation, _FakeNetworkInformation()),
    )
    lifecycle._on_about_to_quit()
    assert calls == [("shutdown", "app_quit_event")]


def test_apple_event_quit_handler_degrades_safely_when_carbon_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ctypes/Carbon failure must not break startup or the hide-on-close policy."""
    import ctypes as ctypes_module

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated Carbon load failure")

    monkeypatch.setattr(ctypes_module, "CDLL", _explode)
    monkeypatch.setattr(
        "stock_watcher.ui.macos._AE_INSTALLED", False
    )
    assert install_apple_event_quit_handler(lambda: None) is False


def test_macos_lifecycle_external_quit_records_graceful_and_exits() -> None:
    app = application()
    window = _LifecycleWindow()
    calls: list[tuple[str, str]] = []
    quit_calls: list[bool] = []

    class _FakeSession:
        def shutdown(self, *, exit_reason: str = "menu_quit") -> None:
            calls.append(("shutdown", exit_reason))

    window.session = _FakeSession()
    lifecycle = MacApplicationLifecycle(
        app,
        cast(Any, window),
        platform="darwin",
        network_information=cast(QNetworkInformation, _FakeNetworkInformation()),
    )
    lifecycle._app.quit = lambda: quit_calls.append(True)  # type: ignore[method-assign]
    lifecycle._handle_external_quit()
    assert calls == [("shutdown", "apple_event_quit")]
    assert quit_calls == [True]
    assert lifecycle._quitting


def test_macos_close_event_programmatic_close_exits_not_hides(
    tmp_path: Path,
) -> None:
    """closeAllWindows-style close (quit AppleEvent) must exit, not hide."""
    application()
    session = ReplaySession(tmp_path / "close-policy.sqlite3")
    window = MainWindow(session)
    window._mac_window_close_policy.enable_background_close()
    assert window._mac_window_close_policy.should_hide_on_close

    event = QCloseEvent()
    assert not event.spontaneous()
    window.closeEvent(event)

    assert event.isAccepted()
    window.close()
