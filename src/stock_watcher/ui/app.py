from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow, ReplaySession

STYLE_SHEET = """
QWidget { font-family: -apple-system, BlinkMacSystemFont, sans-serif; color: #18212b; }
QMainWindow { background: #f4f7fb; }
QGroupBox { border: 1px solid #dbe3ec; border-radius: 12px; margin-top: 10px;
            padding: 16px 12px 12px; background: #ffffff; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; color: #657384; }
#pageTitle { font-size: 28px; font-weight: 700; }
#demoBanner { background: #e9f2ff; border: 1px solid #c4ddff; border-radius: 12px;
              padding: 10px; color: #22578e; }
#healthBadge { border-radius: 10px; padding: 7px 12px;
               background: #dff6e8; color: #17683a; }
#healthBadge[state="STOPPED"], #healthBadge[state="STALE"] {
    background: #ffe3e3; color: #a12626; }
#healthBadge[state="WARMING"] { background: #fff1cf; color: #8a5a00; }
#muted { color: #657384; }
QTableWidget { background: #ffffff; border: 1px solid #dbe3ec; border-radius: 10px;
               gridline-color: #edf1f5; }
QHeaderView::section { background: #f1f5f9; padding: 9px; border: none; color: #657384; }
QPushButton { background: #ffffff; border: 1px solid #c9d5e2; border-radius: 8px;
              padding: 9px 14px; }
QPushButton:hover { background: #edf5ff; border-color: #77aee8; }
#alertPopup { background: #ffffff; border: 1px solid #b6c7da; border-radius: 12px; }
#alertRow { background: #f7faff; border: 1px solid #e0eaf5; border-radius: 8px; }
#popupClose { border: none; padding: 2px 5px; }
#detailTitle { font-size: 21px; font-weight: 700; }
"""


def run() -> int:
    parser = argparse.ArgumentParser(description="StockWatcher Mac Mock/Replay UI Alpha")
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(tempfile.gettempdir()) / "stock-watcher-mac-replay-demo.sqlite3",
        help="demo SQLite path; only synthetic data is written",
    )
    args = parser.parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName("StockWatcher")
    app.setStyleSheet(STYLE_SHEET)
    session = ReplaySession(args.db)
    window = MainWindow(session)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
