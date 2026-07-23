"""Capture the five Mac Replay UI evidence states in a real GUI session.

This intentionally captures only the fixed synthetic demo. It is not a
Windows notification or real-market validation tool.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from stock_watcher.domain import HealthState
from stock_watcher.ui.app import STYLE_SHEET
from stock_watcher.ui.history import HistoryDialog
from stock_watcher.ui.main_window import CandidateDetailDialog, MainWindow, ReplaySession


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, help="directory for PNG evidence")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    app.setStyleSheet(STYLE_SHEET)
    database = Path(tempfile.mkdtemp(prefix="stock-watcher-evidence-")) / "demo.sqlite3"
    session = ReplaySession(database)
    window = MainWindow(session)
    window.show()
    dialogs: list[CandidateDetailDialog | HistoryDialog] = []

    def capture(name: str, widget: object) -> None:
        destination = args.output / f"{name}.png"
        grab = getattr(widget, "grab")
        saved = bool(grab().save(str(destination)))
        print(f"captured {destination} exists={destination.exists()} saved={saved}")

    def healthy_main() -> None:
        if window._popup is not None:
            window._popup.close()
        window._popup = None
        window._last_alert_signature = None
        QTimer.singleShot(250, lambda: (capture("01-healthy-main", window), popup()))

    def popup() -> None:
        window._show_initial_alert()
        QTimer.singleShot(
            450,
            lambda: (
                capture("02-three-row-popup", window._popup),
                stopped(),
            ),
        )

    def stopped() -> None:
        window._stop_replay()
        QTimer.singleShot(250, lambda: (capture("03-stopped", window), detail()))

    def detail() -> None:
        # Re-enter the healthy view without creating a third history entry;
        # the fixed replay evidence should show 09:45 and 09:15 only.
        session.state = HealthState.HEALTHY
        session.health_detail = "恢复完成；新鲜回放样本已通过健康门"
        window._refresh()
        row = next(iter(window._rows.values()))
        dialog = CandidateDetailDialog(row, window)
        dialogs.append(dialog)
        dialog.show()
        QTimer.singleShot(400, lambda: (capture("04-detail", dialog), history(dialog)))

    def history(detail_dialog: CandidateDetailDialog) -> None:
        detail_dialog.close()
        dialog = HistoryDialog(session.store.path, window)
        dialogs.append(dialog)
        dialog.show()
        QTimer.singleShot(900, lambda: (capture("05-history", dialog), finish()))

    def finish() -> None:
        for dialog in dialogs:
            dialog.close()
        window.close()
        app.quit()

    QTimer.singleShot(900, healthy_main)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
