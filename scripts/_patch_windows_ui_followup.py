from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    content = path.read_text(encoding="utf-8")
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{relative}: expected one match, found {count}: {old!r}")
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/stock_watcher/ui/outcome_review.py",
    "from PySide6.QtCore import QTimer\n",
    "from PySide6.QtCore import QThread, QTimer, Signal\n",
)
replace_once(
    "src/stock_watcher/ui/outcome_review.py",
    "\n\nclass OutcomeReviewPanel(QWidget):\n",
    '''

class OutcomeReviewWorker(QThread):
    """Backward-compatible one-shot reader used by older tests and integrations.

    The interactive panel no longer owns this QThread: its close path uses a daemon
    Python worker so closing the dialog never waits on a database query. Keeping the
    small worker type preserves the existing public import without reintroducing the
    blocking lifecycle.
    """

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
''',
)
replace_once(
    "src/stock_watcher/ui/tushare_v1_session.py",
    '''            try:
                self.store.record_daily_summary(summary)
                self._write_local_summary_report(summary)
            except Exception:
                self._set_summary_retry(now)
                return False
''',
    '''            try:
                self.store.record_daily_summary(summary)
                self._write_local_summary_report(summary)
            except Exception as error:
                self._set_summary_retry(now)
                self._summary_issue = (
                    f"盘后回顾暂未生成（{type(error).__name__}），将在60秒后自动重试。"
                )
                return False
''',
)
replace_once(
    "tests/test_automation_reliability.py",
    '''    assert task["state"] == AutomationTaskState.SUCCEEDED.value
''',
    '''    assert task["state"] == AutomationTaskState.SUCCEEDED.value, (
        task,
        session._summary_issue,
    )
''',
)
