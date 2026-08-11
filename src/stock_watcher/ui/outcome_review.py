from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from stock_watcher.domain import (
    CandidateOutcome,
    OutcomeReview,
    OutcomeStatus,
    SettlementMethod,
    build_outcome_review,
)
from stock_watcher.runtime import candidate_outcome_rows
from stock_watcher.storage import SQLiteStore


class OutcomeReviewWorker(QThread):
    loaded = Signal(object, object, str)

    def __init__(self, path: Path, trading_days: int | None) -> None:
        super().__init__()
        self._path = path
        self._trading_days = trading_days

    def run(self) -> None:
        try:
            store = SQLiteStore(self._path, read_only=True)
            records = candidate_outcome_rows(store, trading_days=self._trading_days)
            review = build_outcome_review(records)
            backfill = store.get_app_setting("candidate_outcome_backfill_status")
            self.loaded.emit(review, backfill, "")
        except Exception as error:  # noqa: BLE001 - shown safely in the page
            self.loaded.emit(None, None, f"复盘暂不可读：{type(error).__name__}")


class OutcomeReviewPanel(QWidget):
    ranges = (("近1周", 5), ("近1月", 20), ("全部", None))

    def __init__(self, path: Path, parent: Any = None) -> None:
        super().__init__(parent)
        self._path = path
        self._worker: OutcomeReviewWorker | None = None
        self._range_buttons: dict[int | None, QPushButton] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(12)

        ranges = QHBoxLayout()
        ranges.setSpacing(8)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for label, trading_days in self.ranges:
            button = QPushButton(label)
            button.setObjectName("outcomeRangeButton")
            button.setCheckable(True)
            button.setChecked(trading_days == 20)
            button.clicked.connect(
                lambda _checked=False, days=trading_days: self.load(days)
            )
            group.addButton(button)
            self._range_buttons[trading_days] = button
            ranges.addWidget(button)
        ranges.addStretch(1)
        root.addLayout(ranges)

        self._status = QLabel("正在读取次日复盘…")
        self._status.setObjectName("historyStatus")
        root.addWidget(self._status)

        summary = QGridLayout()
        summary.setHorizontalSpacing(10)
        summary.setVerticalSpacing(10)
        self._overall_win = _metric_card("个股胜率")
        self._average = _metric_card("单笔平均收益")
        self._portfolio = _metric_card("日组合胜率")
        self._settled = _metric_card("已结算 / 总笔数")
        for index, card in enumerate(
            (self._overall_win, self._average, self._portfolio, self._settled)
        ):
            summary.addWidget(card.frame, index // 2, index % 2)
        root.addLayout(summary)

        slots = QHBoxLayout()
        slots.setSpacing(10)
        self._morning = _slot_card("09:45")
        self._afternoon = _slot_card("14:45")
        slots.addWidget(self._morning.frame, 1)
        slots.addWidget(self._afternoon.frame, 1)
        root.addLayout(slots)

        self._median = QLabel("收益率中位数 —")
        self._median.setObjectName("outcomeMedian")
        root.addWidget(self._median)
        self._portfolio_days = QLabel("暂无完整交易日组合")
        self._portfolio_days.setObjectName("outcomePortfolioDays")
        self._portfolio_days.setWordWrap(True)
        root.addWidget(self._portfolio_days)

        records_host = QWidget()
        self._records = QVBoxLayout(records_host)
        self._records.setContentsMargins(0, 0, 0, 0)
        self._records.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(records_host)
        root.addWidget(scroll, 1)

        self._backfill = QLabel(
            "可验证历史已回补；无法验证的数据不计入统计。"
        )
        self._backfill.setObjectName("historyNote")
        self._backfill.setWordWrap(True)
        root.addWidget(self._backfill)
        disclaimer = QLabel(
            "按行情价格进行理论复盘，未计手续费、滑点及实际成交限制，不构成投资建议。"
        )
        disclaimer.setObjectName("outcomeDisclaimer")
        disclaimer.setWordWrap(True)
        root.addWidget(disclaimer)
        self.load(20)

    def load(self, trading_days: int | None) -> None:
        worker = self._worker
        if worker is not None and worker.isRunning():
            return
        self._status.setText("正在读取次日复盘…")
        for days, button in self._range_buttons.items():
            button.setChecked(days == trading_days)
            button.setEnabled(False)
        worker = OutcomeReviewWorker(self._path, trading_days)
        worker.loaded.connect(self._on_loaded)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def wait_for_worker(self, timeout_ms: int = 2000) -> None:
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.wait(timeout_ms)

    def _on_worker_finished(self) -> None:
        for button in self._range_buttons.values():
            button.setEnabled(True)

    def _on_loaded(self, value: object, backfill: object, error: str) -> None:
        if error:
            self._status.setText(error)
            self._clear_records()
            return
        review = value if isinstance(value, OutcomeReview) else build_outcome_review(())
        self._render_statistics(review)
        self._clear_records()
        for record in review.records:
            self._records.addWidget(_record_card(record))
        if review.records:
            self._status.setText(f"共 {len(review.records)} 笔理论复盘记录")
        else:
            empty = QLabel("暂无次日复盘记录\n从下一笔固定09:45或14:45提醒开始记录。")
            empty.setObjectName("outcomeEmpty")
            empty.setMinimumHeight(120)
            empty.setWordWrap(True)
            self._records.addWidget(empty)
            self._status.setText("暂无次日复盘记录")
        if isinstance(backfill, dict) and backfill.get("message"):
            self._backfill.setText(str(backfill["message"]))

    def _render_statistics(self, review: OutcomeReview) -> None:
        self._overall_win.value.setText(_rate(review.overall.win_rate))
        self._average.value.setText(_percent(review.overall.average_return_pct))
        self._portfolio.value.setText(_rate(review.portfolio_win_rate))
        self._settled.value.setText(
            f"{review.overall.settled_count} / {review.overall.total_count}"
        )
        _render_slot(self._morning, review.morning)
        _render_slot(self._afternoon, review.afternoon)
        self._median.setText(
            f"收益率中位数 {_percent(review.overall.median_return_pct)} · "
            f"完整组合日 {review.complete_portfolio_days} 天"
        )
        self._portfolio_days.setText(_portfolio_text(review))

    def _clear_records(self) -> None:
        while self._records.count():
            item = self._records.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()


class _MetricCard:
    def __init__(self, frame: QFrame, value: QLabel) -> None:
        self.frame = frame
        self.value = value


class _SlotCard:
    def __init__(
        self,
        frame: QFrame,
        win_rate: QLabel,
        average: QLabel,
        settled: QLabel,
    ) -> None:
        self.frame = frame
        self.win_rate = win_rate
        self.average = average
        self.settled = settled


def _metric_card(title: str) -> _MetricCard:
    frame = QFrame()
    frame.setObjectName("outcomeMetricCard")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(15, 12, 15, 12)
    label = QLabel(title)
    label.setObjectName("outcomeMetricLabel")
    value = QLabel("—")
    value.setObjectName("outcomeMetricValue")
    layout.addWidget(label)
    layout.addWidget(value)
    return _MetricCard(frame, value)


def _slot_card(title: str) -> _SlotCard:
    frame = QFrame()
    frame.setObjectName("outcomeSlotCard")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(15, 12, 15, 12)
    heading = QLabel(title)
    heading.setObjectName("outcomeSlotTitle")
    win_rate = QLabel("胜率 —")
    average = QLabel("平均收益 —")
    settled = QLabel("已结算 0 笔")
    layout.addWidget(heading)
    layout.addWidget(win_rate)
    layout.addWidget(average)
    layout.addWidget(settled)
    return _SlotCard(frame, win_rate, average, settled)


def _render_slot(card: _SlotCard, stats: Any) -> None:
    card.win_rate.setText(f"胜率 {_rate(stats.win_rate)}")
    card.average.setText(f"平均收益 {_percent(stats.average_return_pct)}")
    card.settled.setText(f"已结算 {stats.settled_count} / {stats.total_count} 笔")


def _record_card(record: CandidateOutcome) -> QFrame:
    card = QFrame()
    card.setObjectName("outcomeRecordCard")
    card.setProperty("state", _record_direction(record))
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 13, 16, 13)
    layout.setSpacing(5)
    heading = QLabel(f"{record.entry_trade_date.isoformat()} · {record.slot.value}")
    heading.setObjectName("outcomeRecordTime")
    security = QLabel(f"{record.name}  {record.code}")
    security.setObjectName("outcomeSecurity")
    prices = QLabel(_prices(record))
    prices.setObjectName("outcomePrices")
    result = QLabel(_result(record))
    result.setObjectName("outcomeReturn")
    result.setProperty("direction", _record_direction(record))
    method = QLabel(_method(record))
    method.setObjectName("outcomeMethod")
    layout.addWidget(heading)
    layout.addWidget(security)
    layout.addWidget(prices)
    layout.addWidget(result)
    layout.addWidget(method)
    return card


def _prices(record: CandidateOutcome) -> str:
    entry = f"¥{record.entry_price:.2f}"
    if record.status is OutcomeStatus.PENDING:
        return f"{entry} → 待结算"
    if record.status is OutcomeStatus.UNAVAILABLE or record.exit_price is None:
        return f"{entry} → 无有效行情"
    return f"{entry} → ¥{record.exit_price:.2f}"


def _result(record: CandidateOutcome) -> str:
    if record.status is OutcomeStatus.PENDING:
        target = record.target_trade_date.isoformat() if record.target_trade_date else "下一交易日"
        return f"目标：{target} {record.target_slot.value}"
    if record.status is OutcomeStatus.UNAVAILABLE:
        return "不计入胜率"
    labels = {"win": "赢", "loss": "亏", "flat": "持平"}
    return f"{_percent(record.return_pct)} · {labels.get(str(record.outcome), '已结算')}"


def _method(record: CandidateOutcome) -> str:
    methods = {
        SettlementMethod.REALTIME_SCAN: "同次全市场扫描结算",
        SettlementMethod.REALTIME_BATCH: "批量实时结算",
        SettlementMethod.HISTORICAL_MINUTE: "一分钟历史行情回补",
    }
    if record.status is OutcomeStatus.PENDING:
        return "等待下一交易日同档行情"
    if record.status is OutcomeStatus.UNAVAILABLE:
        return record.safe_reason or "行情质量不足"
    if record.settlement_method is None:
        return "已结算"
    return methods.get(record.settlement_method, "已结算")


def _record_direction(record: CandidateOutcome) -> str:
    if record.status is not OutcomeStatus.SETTLED or record.return_pct is None:
        return "neutral"
    if record.return_pct > 0:
        return "up"
    if record.return_pct < 0:
        return "down"
    return "neutral"


def _rate(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _percent(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%"


def _portfolio_text(review: OutcomeReview) -> str:
    if not review.portfolios:
        return "暂无完整交易日组合"
    parts = []
    for portfolio in review.portfolios:
        result = (
            f"等权平均 {_percent(portfolio.average_return_pct)}"
            if portfolio.complete
            else f"结算不完整 {portfolio.settled_count}/{portfolio.total_count}"
        )
        parts.append(f"{portfolio.entry_trade_date.isoformat()} · {result}")
    return "\n".join(parts)
