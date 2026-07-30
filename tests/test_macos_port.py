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
from PySide6.QtNetwork import QLocalServer, QNetworkInformation  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMenuBar  # noqa: E402

import stock_watcher.paths as paths_module  # noqa: E402
import stock_watcher.security.credential_store as credential_module  # noqa: E402
import stock_watcher.ui.app as app_module  # noqa: E402
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
)
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
    primary = SingleInstanceGuard(name)
    secondary = SingleInstanceGuard(name)
    activated: list[bool] = []
    primary.activation_requested.connect(lambda: activated.append(True))

    try:
        assert primary.acquire()
        assert not secondary.acquire()
        deadline = time.monotonic() + 1.0
        while not activated and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert activated == [True]
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


def test_macos_lifecycle_warms_on_wake_and_network_recovery() -> None:
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
    lifecycle.handle_reachability(QNetworkInformation.Reachability.Disconnected)
    lifecycle.handle_reachability(QNetworkInformation.Reachability.Online)

    assert window.background_close_enabled
    assert window.restore_calls >= 1
    assert any("系统唤醒" in reason for reason in window.recovery_reasons)
    assert window.network_reasons == ["网络连接已断开，已停止产生新候选。"]
    assert any("网络已恢复" in reason for reason in window.recovery_reasons)
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
