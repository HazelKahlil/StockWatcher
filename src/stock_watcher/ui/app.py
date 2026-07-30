from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from stock_watcher.config import DataSourceConfigRepository, DataSourceMode
from stock_watcher.paths import runtime_paths

from .macos import MacApplicationLifecycle, SingleInstanceGuard
from .main_window import MainWindow, ReplaySession, UiSession
from .tushare_session import TushareDiagnosticSession
from .tushare_v1_session import TushareV1Session

STYLE_SHEET = """
QWidget { font-family: -apple-system, BlinkMacSystemFont, sans-serif; color: #172231; }
QMainWindow, QDialog { background: #f5f7fb; }
QMenuBar { background: #f5f7fb; color: #667487; border: none; }
QMenuBar::item:selected, QMenu::item:selected { background: #e8f1ff; color: #1670df; }
QMenu { background: #ffffff; border: 1px solid #d9e1ec; padding: 6px; }
#appBrand { font-size: 15px; font-weight: 700; color: #364456; }
#testBadge { color: #1670df; background: #e7f1ff; border-radius: 9px; padding: 4px 9px; }
#pageTitle { font-size: 30px; font-weight: 750; color: #142235; }
#summaryCard, #candidateCard, #metricsCard, #reasonCard, #historyCard {
    background: #ffffff; border: 1px solid #e1e7ef; border-radius: 14px;
}
#summaryCard { padding: 4px; }
#summaryLabel, #candidateMeta, #metricLabel { color: #748296; font-size: 13px; }
#summaryValue { font-size: 18px; font-weight: 700; color: #1c2d42; }
#summaryValue[state="connected"] { color: #17814f; }
#summaryValue[state="checking"] { color: #1670df; }
#summaryValue[state="disconnected"] { color: #c63f3f; }
#summaryValue[state="not_applicable"] { color: #667487; }
#candidateCard { min-height: 72px; }
#candidateCard:hover { background: #fbfdff; border-color: #b8d4f8; }
#cardsScroll, #cardsScroll > QWidget > QWidget, #cardsHost {
    background: transparent; border: none;
}
#candidateCard[previous="true"] { background: #f8fafc; border-color: #e5e9ef; }
#candidateCard[previous="true"] #candidateName,
#candidateCard[previous="true"] #candidateChange,
#candidateCard[previous="true"] #candidateSector { color: #8a95a4; }
#rankBadge, #popupRank { background: #eaf3ff; color: #1670df; border-radius: 19px;
                         font-size: 17px; font-weight: 700; }
#candidateName, #popupName { font-size: 20px; font-weight: 700; }
#candidateCode, #popupCode { color: #7d8999; font-size: 14px; }
#candidateChange, #popupChange { color: #df3c3c; font-size: 22px; font-weight: 750; }
#candidatePrice { color: #667487; font-size: 14px; }
#candidateSector { font-size: 16px; font-weight: 600; }
#cardArrow { color: #8e9bad; font-size: 28px; }
#levelBadge { border-radius: 8px; padding: 7px 5px; font-size: 17px; font-weight: 700; }
#levelBadge[level="强"] { color: #d93636; background: #fff0f0; border: 1px solid #ffcaca; }
#levelBadge[level="中"] { color: #b87700; background: #fff7df; border: 1px solid #ffe0a0; }
#levelBadge[level="近"] { color: #6d7785; background: #f0f2f5; border: 1px solid #d8dee6; }
#interruptCard { background: #fffaf0; border: 1px solid #f3dfac; border-radius: 14px; }
#interruptTitle { color: #9a731d; font-size: 22px; font-weight: 750; }
#interruptMessage { color: #39495d; font-size: 16px; }
#issueList { color: #6e5b2d; font-size: 14px; }
#interruptMeta { color: #7c8795; font-size: 14px; }
#emptyState {
    color: #6c798a; background: #f8fafc; border: 1px dashed #ced7e2;
    border-radius: 12px; padding: 24px; font-size: 15px;
}
#sectionTitle { color: #405067; font-size: 17px; font-weight: 700; }
#primaryButton, #secondaryButton { border-radius: 9px; padding: 11px 18px; font-size: 16px; }
#primaryButton { background: #1679ed; border: 1px solid #1679ed; color: #ffffff; }
#primaryButton:hover { background: #0b68d5; }
#primaryButton:disabled { background: #9cbfe6; border-color: #9cbfe6; }
#secondaryButton { background: #ffffff; border: 1px solid #d3dce8; color: #33445a; }
#secondaryButton:hover { background: #eef5ff; border-color: #a8c7ee; }
#secondaryButton:disabled { color: #a2acb9; background: #f5f7fa; }
#footer { color: #7d8999; font-size: 13px; }
#statusDot { font-size: 14px; padding-right: 3px; }
#statusDot[state="healthy"] { color: #35b968; }
#statusDot[state="warming"] { color: #d59a25; }
#statusDot[state="checking"] { color: #1679ed; }
#statusDot[state="stopped"] { color: #d34a4a; }
#alertPopup { background: #ffffff; border: 1px solid #d6e0ec; border-radius: 14px; }
#popupTitle { font-size: 21px; font-weight: 750; }
#popupSubtitle { color: #718096; font-size: 14px; }
#alertRow { background: #fbfdff; border: 1px solid #e1e8f2; border-radius: 10px; }
#alertRow:hover { background: #f1f7ff; border-color: #a9c9f2; }
#popupChange { margin-left: 8px; }
#popupHint, #historyStatus, #historyNote, #dialogDescription { color: #7b8797; font-size: 13px; }
#popupClose { color: #718096; background: transparent; border: none; padding: 3px 5px; }
#popupClose:hover { color: #1670df; background: #eef5ff; }
#dialogTitle { font-size: 25px; font-weight: 750; }
#metricsCard { padding: 4px; }
#metricValue { font-size: 20px; font-weight: 700; margin-top: 4px; }
#metricValue[tone="up"] { color: #df3c3c; }
#metricValue[tone="medium"] { color: #b87700; }
#sectionTitle { margin-top: 4px; }
#reasonCard { padding: 2px; }
#reasonTitle { color: #33445a; font-weight: 700; min-width: 112px; }
#reasonText { color: #667487; }
#conclusion { color: #546579; background: #edf5ff; border-radius: 9px; padding: 12px 14px; }
#historyCard { padding: 2px; }
#historyTime { font-size: 16px; font-weight: 700; }
#historyOverall { color: #8a6b27; font-size: 14px; }
#historyCandidates { color: #4e5d70; font-size: 15px; }
QPushButton { border-radius: 9px; padding: 9px 14px; }
"""


def application_icon_path() -> Path:
    return Path(__file__).with_name("assets") / "stockwatcher.png"


def run(
    *,
    preflight_verified: bool = False,
    terminal_path: Path | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="StockWatcher desktop application")
    parser.add_argument(
        "--provider",
        choices=("tushare", "replay", "tdxquant", "tushare-diagnostic"),
        default="tushare",
    )
    parser.add_argument("--endpoint", default="http://127.0.0.1:17709/")
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite path; defaults to temporary Replay storage or platform app-data",
    )
    args = parser.parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName("StockWatcher")
    app.setOrganizationName("StockWatcher")
    icon_path = application_icon_path()
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyleSheet(STYLE_SHEET)
    instance_guard: SingleInstanceGuard | None = None
    if sys.platform == "darwin":
        instance_guard = SingleInstanceGuard(parent=app)
        if not instance_guard.acquire():
            return 0
    if args.provider == "tdxquant":
        from .tdx_session import TdxDiagnosticSession

        paths = runtime_paths()
        paths.create()
        session: UiSession = TdxDiagnosticSession(
            args.db or paths.database,
            args.endpoint,
            terminal_path=terminal_path,
            preflight_verified=preflight_verified,
        )
    elif args.provider == "replay":
        replay_db = args.db or (
            Path(tempfile.gettempdir()) / "stock-watcher-mac-replay-demo.sqlite3"
        )
        session = ReplaySession(replay_db)
    elif args.provider == "tushare-diagnostic":
        paths = runtime_paths()
        paths.create()
        session = TushareDiagnosticSession(args.db or paths.database)
    else:
        paths = runtime_paths()
        paths.create()
        settings = DataSourceConfigRepository(
            paths.root / "config" / "data-sources.yaml"
        ).load()
        if settings.mode is DataSourceMode.REPLAY:
            session = ReplaySession(args.db or paths.root / "replay-diagnostic.sqlite3")
        elif settings.mode is DataSourceMode.ADVANCED_DIAGNOSTIC:
            session = TushareDiagnosticSession(args.db or paths.database)
        elif settings.mode is DataSourceMode.TDX_DIAGNOSTIC:
            from .tdx_session import TdxDiagnosticSession

            session = TdxDiagnosticSession(
                args.db or paths.database,
                args.endpoint,
                terminal_path=terminal_path,
                preflight_verified=preflight_verified,
            )
        else:
            session = TushareV1Session(
                args.db or paths.database,
                settings=settings,
            )
    window = MainWindow(session)
    if instance_guard is not None:
        lifecycle = MacApplicationLifecycle(app, window)
        window.set_secondary_notification_sender(lifecycle.show_notification)
        instance_guard.activation_requested.connect(window.restore_main_window)
        app.aboutToQuit.connect(instance_guard.close)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
