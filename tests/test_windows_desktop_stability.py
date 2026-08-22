from __future__ import annotations

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
