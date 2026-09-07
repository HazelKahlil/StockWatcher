from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGraphicsOpacityEffect,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
)

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
from stock_watcher.ui.main_window import CandidateCard, MainWindow, ReplaySession
from stock_watcher.ui.popup import AlertPopup
from stock_watcher.ui.presenter import UiSnapshot, format_change, snapshot_from_batch


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


class _ImmediateTester:
    def test(self, _profile: object, _secret: str) -> CredentialTestResult:
        return CredentialTestResult(
            success=True,
            tested_at=datetime.now().astimezone(),
            status_text="通过",
            permission_summary="通过",
            expires_at="未知",
        )


def _pump(app: QApplication, seconds: float = 0.2) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def _close_window(app: QApplication, window: MainWindow) -> None:
    window.request_application_exit()
    window.close()
    _pump(app, 0.3)
    app.processEvents()


def _candidate_cards(window: MainWindow) -> list[CandidateCard]:
    cards: list[CandidateCard] = []
    for index in range(window._cards.count()):
        item = window._cards.itemAt(index)
        widget = item.widget() if item is not None else None
        if isinstance(widget, CandidateCard):
            cards.append(widget)
    return cards


def _card_label(card: CandidateCard, name: str) -> QLabel:
    label = card.findChild(QLabel, name)
    assert label is not None, f"missing {name}"
    return label


def _previous_flag(card: CandidateCard) -> bool:
    value = card.property("previous")
    return value is True or value == "true"


def _assert_previous_style(card: CandidateCard, *, previous: bool) -> None:
    assert _previous_flag(card) is previous
    effect = card.graphicsEffect()
    if previous:
        assert isinstance(effect, QGraphicsOpacityEffect)
    else:
        assert effect is None


def _wait_until(predicate: Callable[[], bool], *, message: str, seconds: float = 1.0) -> None:
    deadline = time.monotonic() + seconds
    app = QApplication.instance()
    while time.monotonic() < deadline:
        if isinstance(app, QApplication):
            app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(message)


def _cards_refresh(root: Path) -> None:
    app = _application()
    session = ReplaySession(root / "cards.sqlite3")
    window = MainWindow(session)
    window.show()
    app.processEvents()
    before = _candidate_cards(window)
    assert len(before) == 3
    for card in before:
        _assert_previous_style(card, previous=False)

    window._active_operation = "check"
    window._refresh_chrome()
    app.processEvents()
    assert _candidate_cards(window) == before

    calls: list[int] = []
    original_snapshot = window._snapshot

    def counted_snapshot() -> UiSnapshot:
        snapshot = original_snapshot()
        calls.append(id(snapshot))
        return snapshot

    window._snapshot = counted_snapshot  # type: ignore[method-assign]
    window._refresh()
    app.processEvents()
    assert len(calls) == 1, calls
    reused = _candidate_cards(window)
    assert reused == before
    window._snapshot = original_snapshot  # type: ignore[method-assign]

    batch = session.batch
    assert batch is not None
    first = batch.candidates[0]
    updated = replace(
        first,
        name="字段更新",
        price=31.25,
        change_pct=9.87,
        sector="更新板块",
        fund_label="资金已确认",
        level="强",
        is_supplement=False,
    )
    session.batch = replace(batch, candidates=(updated, *batch.candidates[1:]))
    window._refresh()
    app.processEvents()
    after_fields = _candidate_cards(window)
    assert after_fields == before
    assert after_fields[0].code == first.code
    assert _card_label(after_fields[0], "candidateName").text() == "字段更新"
    assert _card_label(after_fields[0], "candidatePrice").text() == "¥31.25"
    assert _card_label(after_fields[0], "candidateChange").text() == format_change(9.87)
    assert _card_label(after_fields[0], "candidateSector").text() == "更新板块"
    assert after_fields[0]._fund.text() == "资金已确认"
    assert _card_label(after_fields[0], "levelBadge").text() == "强"

    ordered = session.batch
    assert ordered is not None
    session.batch = replace(
        ordered,
        candidates=(ordered.candidates[1], ordered.candidates[0], *ordered.candidates[2:]),
    )
    window._refresh()
    app.processEvents()
    after_sort = _candidate_cards(window)
    assert [card.code for card in after_sort] == [
        ordered.candidates[1].code,
        ordered.candidates[0].code,
        ordered.candidates[2].code,
    ]
    by_code = {card.code: card for card in after_sort}
    assert by_code[ordered.candidates[0].code] is before[0]
    assert by_code[ordered.candidates[1].code] is before[1]
    assert by_code[ordered.candidates[2].code] is before[2]

    swapped = session.batch
    assert swapped is not None
    replacement = replace(swapped.candidates[2], code="688001", name="换股样本")
    kept = (swapped.candidates[0], swapped.candidates[1], replacement)
    session.batch = replace(swapped, candidates=kept)
    window._refresh()
    app.processEvents()
    after_swap = _candidate_cards(window)
    assert [card.code for card in after_swap] == [
        swapped.candidates[0].code,
        swapped.candidates[1].code,
        "688001",
    ]
    assert after_swap[0] is by_code[swapped.candidates[0].code]
    assert after_swap[1] is by_code[swapped.candidates[1].code]
    assert after_swap[2] not in before
    assert _card_label(after_swap[2], "candidateName").text() == "换股样本"

    session.batch = None
    window._refresh()
    app.processEvents()
    assert _candidate_cards(window) == []
    empty = window.findChild(QLabel, "emptyState")
    assert empty is not None
    assert "观察股票" in empty.text()

    session.batch = replace(swapped, candidates=kept)
    window._refresh()
    app.processEvents()
    restored = _candidate_cards(window)
    assert [card.code for card in restored] == [
        swapped.candidates[0].code,
        swapped.candidates[1].code,
        "688001",
    ]

    session.stop()
    window._refresh()
    app.processEvents()
    stale = _candidate_cards(window)
    assert [card.code for card in stale] == [card.code for card in restored]
    assert stale == restored
    for card in stale:
        _assert_previous_style(card, previous=True)
    assert window._interrupt_card.isVisible()

    session.recover()
    window._refresh()
    app.processEvents()
    healthy = _candidate_cards(window)
    assert healthy
    for card in healthy:
        _assert_previous_style(card, previous=False)
    assert not window._interrupt_card.isVisible()

    _close_window(app, window)


def _panels_lifecycle(root: Path) -> None:
    app = _application()
    session = ReplaySession(root / "panels.sqlite3")
    setattr(
        session,
        "data_source_controller",
        lambda: DataSourceSettingsController(
            store=MemoryCredentialStore(),
            tester=_ImmediateTester(),
        ),
    )
    window = MainWindow(session)
    window.show()
    app.processEvents()
    first_code = next(iter(window._rows))

    window._open_detail_by_code(first_code)
    _pump(app)
    detail = window._candidate_detail_dialog
    assert detail is not None and detail.isVisible()
    window._open_detail_by_code(first_code)
    assert window._candidate_detail_dialog is detail
    detail.close()
    _wait_until(
        lambda: window._candidate_detail_dialog is None,
        message="detail dialog did not close",
    )
    window._open_detail_by_code(first_code)
    _pump(app)
    reopened_detail = window._candidate_detail_dialog
    assert reopened_detail is not None and reopened_detail is not detail
    assert reopened_detail.isVisible()

    openers: tuple[tuple[str, Callable[[], None]], ...] = (
        ("history", window._open_history),
        ("summary", window._open_daily_summary),
        ("info", window._open_developer_info),
        ("settings", window._open_data_source_settings),
    )
    for key, opener in openers:
        opener()
        _pump(app)
        first = window._panels.get(key)
        assert isinstance(first, QDialog) and first.isVisible(), key
        opener()
        assert window._panels.get(key) is first, key
        first.close()

        def panel_closed(current: str = key) -> bool:
            return current not in window._panels

        _wait_until(
            panel_closed,
            message=f"{key} dialog did not close",
        )
        opener()
        _pump(app)
        second = window._panels.get(key)
        assert isinstance(second, QDialog) and second is not first, key
        assert second.isVisible(), key

    assert window._candidate_detail_dialog is not None
    assert set(window._panels) == {"history", "summary", "info", "settings"}
    window._prepare_for_close()
    _pump(app)
    assert window._candidate_detail_dialog is None
    assert window._panels == {}

    window._open_detail_by_code(first_code)
    window._open_history()
    window._open_daily_summary()
    window._open_developer_info()
    window._open_data_source_settings()
    _pump(app)
    assert window._candidate_detail_dialog is not None
    assert set(window._panels) == {"history", "summary", "info", "settings"}
    _close_window(app, window)
    assert window._candidate_detail_dialog is None
    assert window._panels == {}


def _worker_tick(root: Path) -> None:
    app = _application()
    session = ReplaySession(root / "worker.sqlite3")
    window = MainWindow(session)
    window.show()
    app.processEvents()
    before = _candidate_cards(window)
    assert before

    entered = threading.Event()
    release = threading.Event()
    original_recover = session.recover

    def blocking_recover() -> None:
        entered.set()
        release.wait(2.0)
        original_recover()

    session.recover = blocking_recover  # type: ignore[method-assign]
    session.is_replay = False
    window._start_tq_operation("check")
    _wait_until(entered.is_set, message="real worker did not start")
    assert window._operation_thread is not None
    assert window._operation_thread.isRunning()
    assert window._operation_worker is not None

    window._refresh_chrome()
    window._operation_progress_timer.timeout.emit()
    app.processEvents()
    assert _candidate_cards(window) == before
    assert window._operation_thread.isRunning()

    window.request_application_exit()
    window.close()
    app.processEvents()
    assert not window.isVisible()
    assert window._operation_thread is not None
    release.set()

    def worker_finished() -> bool:
        thread = window._operation_thread
        if thread is None:
            return True
        try:
            return not thread.isRunning()
        except RuntimeError:
            return True

    _wait_until(
        worker_finished,
        message="worker thread did not finish after close",
        seconds=2.0,
    )
    _pump(app, 0.3)
    app.processEvents()


SCENARIOS: dict[str, Callable[[Path], None]] = {
    "layout": _layout,
    "close": _close_during_scan,
    "popup": _popup,
    "settings": _settings_close,
    "history": _history_close,
    "cards": _cards_refresh,
    "panels": _panels_lifecycle,
    "worker": _worker_tick,
}


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    names = "|".join(SCENARIOS)
    if len(arguments) != 2 or arguments[0] not in SCENARIOS:
        print(f"usage: windows_ui_probe.py <{names}> <tmp-dir>")
        return 2
    root = Path(arguments[1])
    root.mkdir(parents=True, exist_ok=True)
    SCENARIOS[arguments[0]](root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
