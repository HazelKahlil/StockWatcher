from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QPushButton, QVBoxLayout

from stock_watcher.storage import SQLiteStore


class HistoryWorker(QThread):
    loaded = Signal(object, str)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path

    def run(self) -> None:
        try:
            store = SQLiteStore(self._path, read_only=True)
            rows = store.list_recent_snapshots()
            self.loaded.emit(rows, "")
        except Exception as error:  # noqa: BLE001 - surfaced in the dialog, not swallowed
            self.loaded.emit([], f"历史暂不可读：{error}")


class HistoryDialog(QDialog):
    def __init__(self, path: Path, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("历史记录")
        self.resize(760, 520)
        self._worker = HistoryWorker(path)
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 30, 24)
        root.setSpacing(16)
        title = QLabel("历史记录")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        description = QLabel("这里可以查看之前的提醒结果。")
        description.setObjectName("dialogDescription")
        root.addWidget(description)
        self._status = QLabel("正在读取历史记录…")
        self._status.setObjectName("historyStatus")
        root.addWidget(self._status)
        self._records = QVBoxLayout()
        self._records.setSpacing(10)
        root.addLayout(self._records, 1)
        note = QLabel("历史仅用于回看，不会影响当前结果。")
        note.setObjectName("historyNote")
        root.addWidget(note)
        close = QPushButton("关闭")
        close.setObjectName("secondaryButton")
        close.clicked.connect(self.reject)
        root.addWidget(close)
        self._worker.loaded.connect(self._on_loaded)
        self._worker.start()

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
            timestamp = _display_time(record.get("source_ts"))
            overall = "偏弱" if record.get("overall_weak") else "整体正常"
            card = QFrame()
            card.setObjectName("historyCard")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(18, 14, 18, 14)
            heading = QFrame()
            heading_layout = QVBoxLayout(heading)
            heading_layout.setContentsMargins(0, 0, 0, 0)
            time_label = QLabel(timestamp)
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
