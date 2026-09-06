from __future__ import annotations

import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication, QLabel
from test_candidate_repeat_tracker import make_batch

from stock_watcher.domain import SHANGHAI, HealthState
from stock_watcher.engine import AlertTrigger
from stock_watcher.paths import runtime_paths
from stock_watcher.runtime import AutomationPlanner, AutomationTaskState
from stock_watcher.security import MemoryCredentialStore
from stock_watcher.ui import history
from stock_watcher.ui.popup import AlertPopup
from stock_watcher.ui.presenter import snapshot_from_batch
from stock_watcher.ui.tushare_v1_session import TushareV1Session


def test_packaged_replay_can_isolate_all_runtime_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STOCKWATCHER_RUNTIME_ROOT", str(tmp_path / "preview"))
    paths = runtime_paths()
    paths.create()
    assert all(
        p.is_relative_to(tmp_path)
        for p in (
            paths.root,
            paths.data,
            paths.logs,
            paths.reports,
            paths.database,
        )
    )


@pytest.fixture()
def desktop(tmp_path: Path) -> Any:
    session = TushareV1Session(
        tmp_path / "desktop.sqlite3",
        credential_store=MemoryCredentialStore(),
    )
    session.state = HealthState.HEALTHY
    yield session
    session.shutdown()
    session.store.close()


def test_desktop_reuses_repeat_rules_and_only_intraday_gets_purple(
    desktop: TushareV1Session,
) -> None:
    for day in (1, 2, 3):
        now = datetime(2026, 9, day, 10, 0, tzinfo=SHANGHAI)
        desktop.batch = make_batch(now)
        desktop._record_alert(now, AlertTrigger.INTRADAY, "test", "盘中强异动", "")
        pending = desktop.consume_pending_alert()
        assert pending is not None
        assert bool(pending.repeat_labels) is (day == 3)
    desktop._record_alert(now, AlertTrigger.INTRADAY, "test", "盘中强异动", "")
    states = desktop._repeat_tracker.projections_from_store(["600001.SH"])
    assert states["600001.SH"].occurrence_count == 3
    desktop._record_alert(now, AlertTrigger.SCHEDULED_1445, "test", "14:45 观察提醒", "")
    fixed = desktop.consume_pending_alert()
    assert fixed is not None and fixed.repeat_labels == ()


def test_history_keeps_repeat_state_at_that_trade_date(
    desktop: TushareV1Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for day in (1, 2, 3):
        now = datetime(2026, 9, day, 10, 0, tzinfo=SHANGHAI)
        desktop.batch = make_batch(now)
        desktop._record_alert(now, AlertTrigger.INTRADAY, "test", "盘中强异动", "")

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> FrozenDatetime:
            return cls(2026, 9, 5, 10, 0, tzinfo=SHANGHAI)

    monkeypatch.setattr(history, "datetime", FrozenDatetime)
    worker = history.HistoryWorker(desktop.store.path)
    results: list[tuple[Any, str]] = []
    worker.loaded.connect(lambda rows, error: results.append((rows, error)))
    worker.run()
    rows, error = results[0]
    assert error == ""
    assert len(rows) == 3
    assert [bool(row["repeat_labels"]) for row in rows] == [True, False, False]


def test_popup_renders_repeat_hint_without_changing_rows() -> None:
    app = QApplication.instance() or QApplication([])
    snapshot = snapshot_from_batch(
        make_batch(datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI)),
        health=HealthState.HEALTHY,
    )
    popup = AlertPopup(
        snapshot.candidates,
        "盘中强异动",
        "回放验证",
        lambda _code: None,
        repeat_labels={"600001.SH": "3天3次出现"},
    )
    assert len(popup.findChildren(QLabel, "popupName")) == 3
    assert [label.text() for label in popup.findChildren(QLabel, "repeatHint")] == ["3天3次出现"]
    popup.close()
    app.processEvents()


def test_failed_deadline_keeps_original_failure_timestamp(desktop: TushareV1Session) -> None:
    now = datetime(2026, 9, 3, 9, 45, tzinfo=SHANGHAI)
    spec = AutomationPlanner().for_date(now.date())[0]
    desktop._prepare_automation_tasks(now)
    desktop.store.update_automation_task(
        spec.task_key,
        state="failed",
        updated_at=now.isoformat(),
        detail="original failure",
    )
    desktop._expire_automation_tasks(now + timedelta(hours=1))
    saved = desktop.store.get_automation_task(spec.task_key)
    assert saved is not None
    assert saved["updated_at"] == now.isoformat()
    assert saved["detail"] == "original failure"


def test_independent_summary_timer_does_not_duplicate_scan_summary(
    desktop: TushareV1Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 3, 15, 30, tzinfo=SHANGHAI)
    spec = AutomationPlanner().for_date(now.date())[2]
    desktop._prepare_automation_tasks(now)
    entered, release = threading.Event(), threading.Event()
    calls: list[datetime] = []

    def generate(at: datetime, *, catch_up: bool = False) -> bool:
        calls.append(at)
        entered.set()
        assert release.wait(5)
        return True

    monkeypatch.setattr(desktop, "_generate_summary", generate)
    worker = threading.Thread(target=desktop._execute_summary_task, args=(now, spec))
    worker.start()
    try:
        assert entered.wait(3)
        desktop._execute_summary_task(now, spec)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert calls == [now]
    task = desktop.store.get_automation_task(spec.task_key)
    assert task is not None and task["state"] == AutomationTaskState.SUCCEEDED.value


def test_candidate_card_keyboard_activation_preserves_code() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from stock_watcher.ui.main_window import CandidateCard

    app = QApplication.instance() or QApplication([])
    row = snapshot_from_batch(
        make_batch(datetime(2026, 9, 3, 10, 0, tzinfo=SHANGHAI)),
        health=HealthState.HEALTHY,
    ).candidates[0]
    card = CandidateCard(1, row)
    clicked: list[str] = []
    card.clicked.connect(clicked.append)
    assert card.focusPolicy() == Qt.FocusPolicy.StrongFocus
    QTest.keyClick(card, Qt.Key.Key_Tab)
    assert clicked == []
    QTest.keyClick(card, Qt.Key.Key_Return)
    QTest.keyClick(card, Qt.Key.Key_Space)
    assert clicked == [row.code, row.code]
    assert row.name in card.accessibleName()
    card.close()
    app.processEvents()
