from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread
from typing import Any

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from stock_watcher.domain import SHANGHAI
from stock_watcher.storage import SQLiteStore

from .outcome_review import OutcomeReviewPanel


class HistoryWorker(QThread):
    """Backward-compatible one-shot history reader.

    HistoryDialog itself uses a daemon Python worker so its close path remains
    nonblocking; this class is retained for existing imports and deterministic tests.
    """

    loaded = Signal(object, str)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path

    def run(self) -> None:
        try:
            store = SQLiteStore(self._path, read_only=True)
            rows = store.list_alert_history(
                now=datetime.now(SHANGHAI),
                days=30,
            )
            self.loaded.emit(rows, "")
        except Exception as error:  # noqa: BLE001 - expose only the safe class name
            self.loaded.emit([], f"历史暂不可读：{type(error).__name__}")


class HistoryDialog(QDialog):
    def __init__(self, path: Path, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("历史记录")
        self.resize(860, 720)
        self._path = path
        self._load_generation = 0
        self._load_lock = Lock()
        self._load_result: tuple[int, object, str] | None = None
        self._load_timer = QTimer(self)
        self._load_timer.setInterval(25)
        self._load_timer.timeout.connect(self._poll_history_load)
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 30, 24)
        root.setSpacing(16)
        title = QLabel("历史记录")
        title.setObjectName("dialogTitle")
        root.addWidget(title)

        tabs = QTabWidget()
        self._tabs = tabs
        tabs.setObjectName("historyTabs")
        alerts_page = QWidget()
        alerts_root = QVBoxLayout(alerts_page)
        alerts_root.setContentsMargins(0, 12, 0, 0)
        alerts_root.setSpacing(12)
        description = QLabel("最近30天的09:45、14:45和盘中强异动提醒。")
        description.setObjectName("dialogDescription")
        alerts_root.addWidget(description)
        self._status = QLabel("正在读取历史记录…")
        self._status.setObjectName("historyStatus")
        alerts_root.addWidget(self._status)
        records_host = QWidget()
        self._records = QVBoxLayout(records_host)
        self._records.setContentsMargins(0, 0, 0, 0)
        self._records.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(records_host)
        alerts_root.addWidget(scroll, 1)
        note = QLabel("历史仅用于回看，不会影响当前结果。")
        note.setObjectName("historyNote")
        alerts_root.addWidget(note)
        self._outcomes = OutcomeReviewPanel(path)
        tabs.addTab(alerts_page, "提醒记录")
        tabs.addTab(self._outcomes, "次日复盘")
        root.addWidget(tabs, 1)
        close = QPushButton("关闭")
        close.setObjectName("secondaryButton")
        close.clicked.connect(self.reject)
        root.addWidget(close)
        self._start_history_load()

    def _start_history_load(self) -> None:
        self._load_generation += 1
        generation = self._load_generation
        with self._load_lock:
            self._load_result = None
        Thread(
            target=self._read_history,
            args=(generation,),
            name="stockwatcher-history-read",
            daemon=True,
        ).start()
        self._load_timer.start()

    def _read_history(self, generation: int) -> None:
        try:
            store = SQLiteStore(self._path, read_only=True)
            rows: object = store.list_alert_history(
                now=datetime.now(SHANGHAI),
                days=30,
            )
            error = ""
        except Exception as exc:  # noqa: BLE001 - expose only the safe class name
            rows = []
            error = f"历史暂不可读：{type(exc).__name__}"
        with self._load_lock:
            if generation == self._load_generation:
                self._load_result = (generation, rows, error)

    def _poll_history_load(self) -> None:
        with self._load_lock:
            result = self._load_result
            self._load_result = None
        if result is None:
            return
        generation, rows, error = result
        if generation != self._load_generation:
            return
        self._load_timer.stop()
        self._on_loaded(rows, error)

    def done(self, result: int) -> None:
        self._load_generation += 1
        self._load_timer.stop()
        self._outcomes.cancel_pending_loads()
        super().done(result)

    def _on_loaded(self, rows: object, error: str) -> None:
        if error:
            self._status.setText(error)
            return
        records = (
            [record for record in rows if isinstance(record, dict)]
            if isinstance(rows, list)
            else []
        )
        for record in records:
            if not isinstance(record, dict):
                continue
            payload = _json_dict(record.get("payload_json"))
            candidates = _candidate_names(payload.get("candidates", []))
            timestamp = _display_time(record.get("displayed_at"))
            overall = "偏弱" if record.get("overall_weak") else "整体正常"
            card = QFrame()
            card.setObjectName("historyCard")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(18, 14, 18, 14)
            heading = QFrame()
            heading_layout = QVBoxLayout(heading)
            heading_layout.setContentsMargins(0, 0, 0, 0)
            trigger = _trigger_label(record.get("trigger_type"))
            time_label = QLabel(f"{timestamp} · {trigger}")
            time_label.setObjectName("historyTime")
            status_label = QLabel(overall)
            status_label.setObjectName("historyOverall")
            heading_layout.addWidget(time_label)
            heading_layout.addWidget(status_label)
            layout.addWidget(heading)
            names = QLabel(candidates or "暂无候选")
            names.setObjectName("historyCandidates")
            names.setWordWrap(True)
            layout.addWidget(names)
            self._records.addWidget(card)
        self._status.setText("" if records else "暂无历史提醒记录")


def _json_dict(value: object) -> dict[str, object]:
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _candidate_names(value: object) -> str:
    if not isinstance(value, list):
        return ""
    names = [
        str(candidate.get("name", ""))
        for candidate in value[:3]
        if isinstance(candidate, dict) and candidate.get("name")
    ]
    return "、".join(names)


def _display_time(value: object) -> str:
    if not isinstance(value, str):
        return "—"
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _trigger_label(value: object) -> str:
    labels = {
        "scheduled-09:45": "09:45 观察提醒",
        "scheduled-14:45": "14:45 观察提醒",
        "intraday": "盘中强异动",
    }
    return labels.get(str(value), "观察提醒")
