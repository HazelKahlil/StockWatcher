from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QScrollArea

from stock_watcher.domain import HealthState
from stock_watcher.security import PRIMARY_CREDENTIAL, MemoryCredentialStore
from stock_watcher.storage import SQLiteStore
from stock_watcher.ui.data_source_settings import (
    DataSourceSettingsController,
    DataSourceSettingsDialog,
)
from stock_watcher.ui.data_source_status import CredentialTestResult
from stock_watcher.ui.demo import demo_batch, demo_clock
from stock_watcher.ui.history import HistoryDialog
from stock_watcher.ui.main_window import MainWindow, ReplaySession
from stock_watcher.ui.popup import AlertPopup
from stock_watcher.ui.presenter import snapshot_from_batch


def _application() -> QApplication:
    existing = QApplication.instance()
    app = existing if isinstance(existing, QApplication) else QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def _layout(root: Path) -> None:
    app = _application()
    window = MainWindow(ReplaySession(root / "layout.sqlite3"))
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


def _close_during_scan(root: Path) -> None:
    app = _application()
    session = ReplaySession(root / "close.sqlite3")
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


def _popup(_root: Path) -> None:
    app = _application()
    snapshot = snapshot_from_batch(demo_batch(demo_clock()), health=HealthState.HEALTHY)
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


def _settings_close(_root: Path) -> None:
    app = _application()
    entered = threading.Event()
    release = threading.Event()

    class BlockingTester:
        def test(self, _profile: object, _secret: str) -> CredentialTestResult:
            entered.set()
            release.wait(2.0)
            return CredentialTestResult(
                success=True,
                tested_at=datetime.now().astimezone(),
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


def _history_close(root: Path) -> None:
    app = _application()
    entered = threading.Event()
    release = threading.Event()
    original = SQLiteStore.list_alert_history

    def blocking_history(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        entered.set()
        release.wait(2.0)
        return []

    setattr(SQLiteStore, "list_alert_history", blocking_history)
    try:
        dialog = HistoryDialog(root / "history.sqlite3")
        assert entered.wait(1.0)
        started = time.monotonic()
        dialog.reject()
        assert time.monotonic() - started < 0.2
        release.set()
        app.processEvents()
    finally:
        setattr(SQLiteStore, "list_alert_history", original)
        release.set()


SCENARIOS: dict[str, Callable[[Path], None]] = {
    "layout": _layout,
    "close": _close_during_scan,
    "popup": _popup,
    "settings": _settings_close,
    "history": _history_close,
}


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2 or arguments[0] not in SCENARIOS:
        print("usage: windows_ui_probe.py <layout|close|popup|settings|history> <tmp-dir>")
        return 2
    root = Path(arguments[1])
    root.mkdir(parents=True, exist_ok=True)
    SCENARIOS[arguments[0]](root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
