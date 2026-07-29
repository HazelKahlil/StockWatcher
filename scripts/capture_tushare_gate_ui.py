from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from stock_watcher.ui.main_window import MainWindow  # noqa: E402
from stock_watcher.ui.tushare_session import TushareDiagnosticSession  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    with tempfile.TemporaryDirectory(prefix="stockwatcher-tushare-ui-") as temp:
        session = TushareDiagnosticSession(Path(temp) / "ui.sqlite3")
        window = MainWindow(session)
        window.show()

        def capture() -> None:
            window.grab().save(str(args.output))
            window.close()
            app.quit()

        QTimer.singleShot(700, capture)
        exit_code = app.exec()
    if not args.output.is_file() or args.output.stat().st_size == 0:
        return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
