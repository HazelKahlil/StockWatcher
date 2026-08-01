from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from stock_watcher.domain import SHANGHAI
from stock_watcher.paths import report_directory_for_database
from stock_watcher.runtime.post_close_pdf import (
    REPORT_RETENTION_DAYS,
    list_post_close_report_dates,
    prune_post_close_reports,
    render_post_close_pdf,
)
from stock_watcher.storage import SQLiteStore


class DailySummaryDialog(QDialog):
    def __init__(
        self,
        path: Path,
        parent: Any = None,
        *,
        today: date | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        self._today = today or datetime.now(SHANGHAI).date()
        self._reports_dir = report_directory_for_database(path)
        self._summaries = self._load_recent(path)
        self.setWindowTitle("盘后回顾")
        self.resize(760, 620)
        root = QVBoxLayout(self)
        root.setContentsMargins(30, 26, 30, 24)
        root.setSpacing(14)

        title = QLabel("今日A股盘后回顾 / 历史报告")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        description = QLabel(
            "15:30自动生成固定A4三页PDF。请选择最近31个自然日内的报告查看或下载。"
        )
        description.setObjectName("dialogDescription")
        description.setWordWrap(True)
        root.addWidget(description)

        selector_row = QHBoxLayout()
        selector_label = QLabel("选择日期")
        selector_label.setObjectName("fieldLabel")
        self.date_selector = QComboBox()
        self.date_selector.setObjectName("reportDateSelector")
        self.date_selector.setMinimumHeight(36)
        selector_row.addWidget(selector_label)
        selector_row.addWidget(self.date_selector, 1)
        root.addLayout(selector_row)

        scroll_host = QWidget()
        self._content = QVBoxLayout(scroll_host)
        self._content.setContentsMargins(0, 0, 8, 0)
        self._content.setSpacing(14)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(scroll_host)
        root.addWidget(scroll, 1)

        actions = QHBoxLayout()
        self.download = QPushButton("下载 PDF")
        self.download.setObjectName("primaryButton")
        self.download.setMinimumHeight(40)
        close = QPushButton("关闭")
        close.setObjectName("secondaryButton")
        close.setMinimumHeight(40)
        actions.addWidget(self.download, 2)
        actions.addWidget(close, 1)
        root.addLayout(actions)

        self._populate_dates()
        self.date_selector.currentIndexChanged.connect(self._render_selected)
        self.download.clicked.connect(self._download_selected)
        close.clicked.connect(self.accept)
        self._render_selected()

    def _populate_dates(self) -> None:
        for trade_date in sorted(self._summaries, reverse=True):
            label = f"{trade_date}（今天）" if trade_date == self._today.isoformat() else trade_date
            self.date_selector.addItem(label, trade_date)
        has_reports = self.date_selector.count() > 0
        self.date_selector.setEnabled(has_reports)
        self.download.setEnabled(has_reports)

    def _render_selected(self) -> None:
        self._clear_content()
        trade_date = self._selected_trade_date()
        summary = self._summaries.get(trade_date) if trade_date else None
        if summary is None:
            empty = QLabel(
                "最近31个自然日内还没有盘后回顾。交易日15:30生成成功后会保留在这里。"
            )
            empty.setObjectName("emptyState")
            empty.setWordWrap(True)
            self._content.addWidget(empty)
            self._content.addStretch()
            return

        retrospective = summary.get("version") == "daily-summary-retrospective-v1"
        if retrospective:
            note = QLabel(
                "这是真实静态收盘数据回顾，不是盘中实时Top 3、09:45/14:45 Live"
                "或Windows验收证据。"
            )
        else:
            note = QLabel(
                "基于收盘后的全市场日线、行业宽度和前三日背景生成；"
                "手动查看不计入自动提醒限额。"
            )
        note.setObjectName("dialogDescription")
        note.setWordWrap(True)
        self._content.addWidget(note)

        self._section(
            "今日自动提醒",
            (
                f"{summary['alert_count']} 次（本轮未在盘中持续运行）"
                if retrospective
                else f"{summary['alert_count']} 次"
            ),
        )
        sectors = "、".join(
            str(item[0])
            for item in summary.get("top_sectors", [])
            if isinstance(item, list) and item
        ) or "无满足硬门的行业"
        self._section("强势行业", sectors)
        candidates = "、".join(
            str(item[0])
            for item in summary.get("repeated_candidates", [])
            if isinstance(item, list) and item
        ) or "未形成三只"
        self._section("盘后观察Top3", candidates)
        performance = summary.get("closing_performance", [])
        performance_copy = (
            "；".join(
                _performance_text(item)
                for item in performance
                if isinstance(item, dict)
            )
            or "暂无可核对的收盘表现"
        )
        self._section("收盘Top3", performance_copy)
        self._section("资金状态", str(summary.get("fund_summary", "资金未确认")))
        self._section("数据情况", str(summary.get("health_summary", "未记录")))
        conclusion = QLabel(str(summary.get("summary_text", "")))
        conclusion.setObjectName("conclusion")
        conclusion.setWordWrap(True)
        self._content.addWidget(conclusion)
        self._content.addStretch()

    def _section(self, title: str, value: str) -> None:
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
        self._content.addWidget(card)

    def _download_selected(self) -> None:
        trade_date = self._selected_trade_date()
        if trade_date is None:
            return
        try:
            internal_pdf = self._ensure_internal_pdf(trade_date)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "PDF生成失败", f"暂时无法生成这份报告：{exc}")
            return
        default = Path.home() / "Downloads" / f"{trade_date}-A股盘后回顾.pdf"
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "下载盘后回顾 PDF",
            str(default),
            "PDF 文档 (*.pdf)",
        )
        if not selected:
            return
        destination = Path(selected)
        if destination.suffix.casefold() != ".pdf":
            destination = destination.with_suffix(".pdf")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.resolve() != internal_pdf.resolve():
                shutil.copy2(internal_pdf, destination)
        except OSError as exc:
            QMessageBox.warning(self, "下载失败", f"无法保存PDF：{exc}")
            return
        QMessageBox.information(self, "下载完成", f"PDF已保存到：\n{destination}")

    def _ensure_internal_pdf(self, trade_date: str) -> Path:
        stem = f"{trade_date}-A股盘后回顾"
        pdf_path = self._reports_dir / f"{stem}.pdf"
        if pdf_path.is_file():
            return pdf_path
        record_path = self._reports_dir / f"{stem}.json"
        if record_path.is_file():
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError("报告JSON格式无效")
        else:
            summary = self._summaries.get(trade_date)
            if summary is None:
                raise ValueError("没有找到这一天的报告数据")
            record = dict(summary)
        return render_post_close_pdf(record, pdf_path)

    def _selected_trade_date(self) -> str | None:
        value = self.date_selector.currentData()
        return str(value) if value else None

    def _load_recent(self, path: Path) -> dict[str, dict[str, Any]]:
        cutoff = self._today - timedelta(days=REPORT_RETENTION_DAYS - 1)
        summaries: dict[str, dict[str, Any]] = {}
        if path.is_file():
            writable = SQLiteStore(path)
            writable.prune_daily_summaries(before=cutoff)
            reader = SQLiteStore(path, read_only=True)
            summaries.update(
                {
                    str(item["trade_date"]): item
                    for item in reader.list_daily_summaries(since=cutoff)
                }
            )
        prune_post_close_reports(
            self._reports_dir,
            reference_date=self._today,
        )
        for trade_date in list_post_close_report_dates(
            self._reports_dir,
            reference_date=self._today,
        ):
            if trade_date in summaries:
                continue
            record_path = self._reports_dir / f"{trade_date}-A股盘后回顾.json"
            if not record_path.is_file():
                continue
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(record, dict):
                summaries[trade_date] = _summary_from_record(record)
        return summaries

    def _clear_content(self) -> None:
        while self._content.count():
            item = self._content.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()


def _summary_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    market_value = record.get("market")
    market: Mapping[str, Any] = market_value if isinstance(market_value, dict) else {}
    top3_value = record.get("top3")
    top3: list[Any] = top3_value if isinstance(top3_value, list) else []
    sectors_value = record.get("top_sectors")
    sectors: list[Any] = sectors_value if isinstance(sectors_value, list) else []
    retrospective = str(record.get("verdict", "")).casefold() == "retrospective_only"
    top_names = "、".join(
        str(item.get("name", item.get("code", "")))
        for item in top3[:3]
        if isinstance(item, dict)
    )
    up_ratio = market.get("up_ratio")
    median = market.get("median_change_pct")
    market_copy = (
        f"全市场上涨比例 {float(up_ratio):.1%}，涨跌幅中位数 {float(median):+.2f}%。"
        if isinstance(up_ratio, (int, float)) and isinstance(median, (int, float))
        else "市场宽度数据详见PDF。"
    )
    return {
        "trade_date": str(record.get("trade_date", "")),
        "generated_at": str(record.get("generated_at", "")),
        "alert_count": int(record.get("intraday_alert_count", 0) or 0),
        "top_sectors": [
            [str(item.get("name", "未分类")), int(item.get("strong_count", 0) or 0)]
            for item in sectors[:3]
            if isinstance(item, dict)
        ],
        "repeated_candidates": [
            [str(item.get("name", item.get("code", ""))), 1]
            for item in top3[:3]
            if isinstance(item, dict)
        ],
        "closing_performance": [
            {
                "code": item.get("code", ""),
                "name": item.get("name", ""),
                "close_price": item.get("close"),
                "change_pct": item.get("change_pct"),
                "sector": item.get("sector", ""),
            }
            for item in top3[:3]
            if isinstance(item, dict)
        ],
        "fund_summary": str(
            record.get("fund_summary", "资金未确认，本次排序未使用资金项。")
        ),
        "health_summary": _health_summary(record),
        "summary_text": (
            f"{record.get('trade_date', '')} A股盘后回顾。{market_copy}"
            f"盘后观察Top3：{top_names or '未形成'}。"
        ),
        "version": (
            "daily-summary-retrospective-v1"
            if retrospective
            else "daily-summary-market-review-v1"
        ),
    }


def _health_summary(record: Mapping[str, Any]) -> str:
    coverage = record.get("source_coverage")
    if not isinstance(coverage, dict):
        return "数据覆盖详见PDF。"
    stock_records = coverage.get("stock_records")
    daily_records = coverage.get("daily_records")
    maximum = 0
    if isinstance(daily_records, dict):
        maximum = max(
            (
                int(value)
                for value in daily_records.values()
                if isinstance(value, (int, float))
            ),
            default=0,
        )
    return f"股票资料 {int(stock_records or 0):,} 条；最大单日日线覆盖 {maximum:,} 条。"


def _performance_text(item: dict[str, object]) -> str:
    name = str(item.get("name", item.get("code", "")))
    close = item.get("close_price")
    change = item.get("change_pct")
    sector = str(item.get("sector", ""))
    if isinstance(close, (int, float)) and isinstance(change, (int, float)):
        suffix = f" · {sector}" if sector else ""
        return f"{name} ¥{float(close):.2f} · {float(change):+.2f}%{suffix}"
    value = item.get("change_to_close_pct")
    return (
        f"{name} {float(value):+.2f}%"
        if isinstance(value, (int, float))
        else f"{name} 待确认"
    )
