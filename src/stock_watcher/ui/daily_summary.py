from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from stock_watcher.domain import SHANGHAI
from stock_watcher.storage import SQLiteStore


class DailySummaryDialog(QDialog):
    def __init__(self, path: Path, parent: Any = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("收盘总结")
        self.resize(720, 560)
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 30, 24)
        root.setSpacing(14)
        summary = self._latest(path)
        retrospective = (
            summary is not None
            and summary.get("version") == "daily-summary-retrospective-v1"
        )
        market_review = (
            summary is not None
            and summary.get("version") == "daily-summary-market-review-v1"
        )
        title = QLabel(
            "今日盘后回顾（历史数据测试）"
            if retrospective
            else "今日A股盘后回顾"
            if market_review
            else "今日收盘总结"
        )
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        scroll_host = QWidget()
        content = QVBoxLayout(scroll_host)
        content.setContentsMargins(0, 0, 8, 0)
        content.setSpacing(14)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(scroll_host)
        root.addWidget(scroll, 1)
        if summary is None:
            empty = QLabel("今日总结将在交易日15:30生成。")
            empty.setObjectName("emptyState")
            empty.setWordWrap(True)
            content.addWidget(empty)
        else:
            if retrospective:
                note = QLabel(
                    "仅使用今日收盘后的历史行情回溯；不代表盘中曾发出提醒，"
                    "也不替代真实交易时段验收。"
                )
                note.setObjectName("dialogDescription")
                note.setWordWrap(True)
                content.addWidget(note)
            elif market_review:
                note = QLabel(
                    "基于收盘后的全市场日线、行业宽度和前三日背景生成；"
                    "自动提醒次数单独列示，手动查看不计入提醒限额。"
                )
                note.setObjectName("dialogDescription")
                note.setWordWrap(True)
                content.addWidget(note)
            self._section(
                content,
                (
                    "真实提醒"
                    if retrospective
                    else "今日自动提醒"
                    if market_review
                    else "今日提醒"
                ),
                (
                    f"{summary['alert_count']} 次（本轮未在盘中持续运行）"
                    if retrospective
                    else f"{summary['alert_count']} 次"
                ),
            )
            sectors = "、".join(name for name, _ in summary["top_sectors"]) or "无"
            self._section(
                content,
                "强势行业" if retrospective or market_review else "重点板块",
                sectors,
            )
            repeated = (
                "、".join(
                    f"{name}（{count}次）"
                    for name, count in summary["repeated_candidates"]
                )
                or "无多次重复股票"
            )
            self._section(
                content,
                (
                    "回溯观察Top3"
                    if retrospective
                    else "盘后观察Top3"
                    if market_review
                    else "多次出现"
                ),
                (
                    "、".join(
                        name
                        for name, _ in summary["repeated_candidates"]
                    )
                    if retrospective or market_review
                    else repeated
                ),
            )
            performance = summary["closing_performance"]
            performance_copy = (
                "；".join(
                    _performance_text(item)
                    for item in performance
                    if isinstance(item, dict)
                )
                or "暂无可核对的收盘表现"
            )
            self._section(
                content,
                "收盘Top3" if market_review else "收盘表现",
                performance_copy,
            )
            self._section(content, "资金状态", str(summary["fund_summary"]))
            self._section(content, "数据情况", str(summary["health_summary"]))
            conclusion = QLabel(str(summary["summary_text"]))
            conclusion.setObjectName("conclusion")
            conclusion.setWordWrap(True)
            content.addWidget(conclusion)
        content.addStretch()
        close = QPushButton("关闭")
        close.setObjectName("primaryButton")
        close.clicked.connect(self.accept)
        root.addWidget(close)

    @staticmethod
    def _section(root: QVBoxLayout, title: str, value: str) -> None:
        card = QFrame()
        card.setObjectName("reasonCard")
        layout = QVBoxLayout(card)
        heading = QLabel(title)
        heading.setObjectName("reasonTitle")
        copy = QLabel(value)
        copy.setObjectName("reasonText")
        copy.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(copy)
        root.addWidget(card)

    @staticmethod
    def _latest(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        store = SQLiteStore(path, read_only=True)
        today = datetime.now(SHANGHAI).date().isoformat()
        return store.get_daily_summary(today)


def _performance_text(item: dict[str, object]) -> str:
    name = str(item.get("name", item.get("code", "")))
    close = item.get("close_price")
    change = item.get("change_pct")
    sector = str(item.get("sector", ""))
    if isinstance(close, (int, float)) and isinstance(change, (int, float)):
        suffix = f" · {sector}" if sector else ""
        return f"{name} ¥{float(close):.2f} · {float(change):+.2f}%{suffix}"
    value = item.get("change_to_close_pct")
    return f"{name} {float(value):+.2f}%" if isinstance(value, (int, float)) else f"{name} 待确认"
