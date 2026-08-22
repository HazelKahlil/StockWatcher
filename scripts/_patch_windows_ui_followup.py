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
