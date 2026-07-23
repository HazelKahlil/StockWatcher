from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

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
        self.setWindowTitle("历史批次 · 只读回放")
        self.resize(720, 360)
        self._worker = HistoryWorker(path)
        layout = QVBoxLayout(self)
        self._status = QLabel("正在读取可见批次…（后台只读查询）")
        layout.addWidget(self._status)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["时间", "健康", "整体", "候选", "来源"])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
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
        self._table.setRowCount(len(records))
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            payload = _json_dict(record.get("payload_json"))
            codes = _candidate_codes(payload.get("candidates", []))
            values = (
                str(record.get("source_ts", "")),
                str(record.get("health", "")),
                "偏弱" if record.get("overall_weak") else "正常",
                codes or "无候选",
                str(record.get("provider_version", "")),
            )
            for column, value in enumerate(values):
                self._table.setItem(index, column, QTableWidgetItem(value))
        self._status.setText(f"已读取 {len(records)} 个可见批次；历史只读，不生成新候选。")


def _json_dict(value: object) -> dict[str, object]:
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _candidate_codes(value: object) -> str:
    if not isinstance(value, list):
        return ""
    codes = [
        str(candidate.get("code", ""))
        for candidate in value[:3]
        if isinstance(candidate, dict)
    ]
    return ", ".join(codes)
