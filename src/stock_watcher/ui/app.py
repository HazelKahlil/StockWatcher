from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from stock_watcher.build_info import source_commit
from stock_watcher.config import DataSourceConfigRepository, DataSourceMode
from stock_watcher.paths import runtime_paths
from stock_watcher.startup import StartupRecorder

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
#pageTitle { font-size: 28px; font-weight: 750; color: #142235; }
#summaryCard, #candidateCard, #metricsCard, #reasonCard, #historyCard {
    background: #ffffff; border: 1px solid #e1e7ef; border-radius: 14px;
}
#summaryCard { padding: 0; }
#summaryLabel, #candidateMeta, #metricLabel { color: #748296; font-size: 12px; }
#summaryValue { font-size: 16px; font-weight: 700; color: #1c2d42; }
#summaryValue[state="connected"] { color: #17814f; }
#summaryValue[state="checking"] { color: #1670df; }
#summaryValue[state="disconnected"] { color: #c63f3f; }
#summaryValue[state="not_applicable"] { color: #667487; }
#candidateCard { min-height: 96px; }
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
#interruptCard { background: #fffaf0; border: 1px solid #f3dfac; border-radius: 12px; }
#interruptTitle { color: #9a731d; font-size: 18px; font-weight: 750; }
#interruptMessage { color: #39495d; font-size: 14px; }
#issueList { color: #6e5b2d; font-size: 13px; }
#interruptMeta { color: #7c8795; font-size: 12px; }
#emptyState {
    color: #6c798a; background: #f8fafc; border: 1px dashed #ced7e2;
    border-radius: 12px; padding: 36px; font-size: 16px;
}
#sectionTitle { color: #405067; font-size: 20px; font-weight: 750; }
#primaryButton, #secondaryButton, #dangerButton {
    border-radius: 9px; padding: 11px 18px; font-size: 16px;
}
#primaryButton { background: #1679ed; border: 1px solid #1679ed; color: #ffffff; }
#primaryButton:hover { background: #0b68d5; }
#primaryButton:disabled { background: #9cbfe6; border-color: #9cbfe6; }
#secondaryButton { background: #ffffff; border: 1px solid #d3dce8; color: #33445a; }
#secondaryButton:hover { background: #eef5ff; border-color: #a8c7ee; }
#secondaryButton:disabled { color: #a2acb9; background: #f5f7fa; }
#dangerButton { background: #ffffff; border: 1px solid #e2b8b8; color: #b64242; }
#dangerButton:hover { background: #fff2f2; border-color: #d77878; }
#dangerButton:disabled { color: #b4a3a3; background: #f7f7f7; border-color: #e4e4e4; }
#dataSourceEditor { background: #fbfcfe; border: 1px solid #e1e7ef; border-radius: 12px; }
#dataSourceEditor::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }
#tokenInput {
    background: #ffffff; border: 1px solid #afbed0; border-radius: 9px;
    color: #1c2d42; padding: 9px 12px;
}
#tokenInput:hover { border-color: #7ea8dc; }
#tokenInput:focus { background: #ffffff; border: 2px solid #1679ed; }
#tokenInputHint, #dataSourcePermission { color: #6f7e91; font-size: 13px; }
#dataSourceValue, #dataSourceStatus { color: #34465d; }
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
    assets = Path(__file__).with_name("assets")
    if sys.platform == "darwin":
        macos_icon = assets / "stockwatcher-macos.png"
        if macos_icon.is_file():
            return macos_icon
    return assets / "stockwatcher.png"


def _restore_window(
    window: MainWindow,
    _request: dict[str, object] | None = None,
) -> dict[str, object]:
    window.restore_main_window()
    app = QApplication.instance()
    if app is not None:
        app.setActiveWindow(window)
    QTimer.singleShot(0, window.restore_main_window)
    if sys.platform == "darwin":
        _request_macos_application_activation()
    if app is not None:
        app.processEvents()
    minimized = bool(window.windowState() & Qt.WindowState.WindowMinimized)
    visible = window.isVisible() and not minimized
    application_active = _application_is_active(app)
    ok = visible and not minimized and application_active
    return {
        "ok": ok,
        "result": "success" if ok else "activation-failed",
        "window_visible": visible,
        "window_minimized": minimized,
        "application_active": application_active,
        "activation_timestamp": _timestamp(),
        "error_reason": None if ok else "窗口可见性或应用前台状态未确认",
    }


def _application_is_active(app: QApplication | None) -> bool:
    if app is None:
        return False
    if sys.platform != "darwin":
        return True
    return app.applicationState() is Qt.ApplicationState.ApplicationActive


def _request_macos_application_activation() -> None:
    try:
        subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                (
                    'tell application "System Events" to set frontmost of first process '
                    "whose unix id is "
                    f"{os.getpid()} to true"
                ),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat()


def run(
    *,
    preflight_verified: bool = False,
    terminal_path: Path | None = None,
) -> int:
    recorder = StartupRecorder()
    try:
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
        try:
            args = parser.parse_args()
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 1
            if code == 0:
                recorder.finish(0, "startup_argument_help")
            else:
                recorder.record_error(
                    error,
                    app_available=False,
                    stage="startup_argument_error",
                )
                recorder.finish(code, "startup_argument_error")
            return code
        app = QApplication(sys.argv)
        app.setApplicationName("StockWatcher")
        app.setOrganizationName("StockWatcher")
        icon_path = application_icon_path()
        if icon_path.is_file():
            app.setWindowIcon(QIcon(str(icon_path)))
        app.setStyleSheet(STYLE_SHEET)
        recorder.stage("qapplication-created")
        instance_guard: SingleInstanceGuard | None = None
        if sys.platform == "darwin":
            recorder.stage("single-instance-check")
            instance_guard = SingleInstanceGuard(
                parent=app,
                app_path=recorder.data.get("app_path", ""),
                source_commit=source_commit(),
            )
            if not instance_guard.acquire():
                recorder.stage(
                    "secondary_activated"
                    if instance_guard.last_activation_status == "success"
                    else "secondary_activation_failed",
                    activation_status=instance_guard.last_activation_status,
                    ack=instance_guard.last_activation_ack,
                )
                if instance_guard.last_activation_status != "success":
                    _show_secondary_failure(instance_guard, recorder.log_path)
                    recorder.finish(1, "secondary_activation_failed")
                    return 1
                recorder.finish(0, "secondary_activated")
                return 0
            recorder.stage("primary_started")
        paths = runtime_paths()
        paths.create()
        recorder.set_paths(paths)
        recorder.stage("settings-loaded")
        if args.provider == "tdxquant":
            from .tdx_session import TdxDiagnosticSession

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
            session = TushareDiagnosticSession(args.db or paths.database)
        else:
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
                session = TushareV1Session(args.db or paths.database, settings=settings)
        recorder.stage("session-created")
        window = MainWindow(session)
        recorder.stage("window-created")
        if instance_guard is not None:
            lifecycle = MacApplicationLifecycle(app, window)
            window.set_secondary_notification_sender(lifecycle.show_notification)

            def handle_activation(request: dict[str, object]) -> dict[str, object]:
                recorder.stage(
                    "activation_received",
                    secondary_pid=request.get("secondary_pid"),
                    secondary_app_path=request.get("secondary_app_path"),
                    secondary_source_commit=request.get("secondary_source_commit"),
                )
                result = _restore_window(window, request)
                recorder.stage("window-restored", **result)
                return result

            instance_guard.set_activation_handler(handle_activation)
            app.aboutToQuit.connect(instance_guard.close)
        window.show()
        recorder.stage("window-shown")
        recorder.stage("event-loop-entered")
        exit_code = app.exec()
        recorder.finish(exit_code, "normal-exit")
        return exit_code
    except BaseException as error:
        recorder.fatal(error, app_available=QApplication.instance() is not None)
        recorder.finish(1, "fatal-startup-error")
        return 1


def _show_secondary_failure(
    instance_guard: SingleInstanceGuard,
    log_path: Path,
) -> None:
    status = instance_guard.last_activation_status
    ack = instance_guard.last_activation_ack
    primary_path = str(ack.get("primary_app_path", "未知"))
    primary_commit = str(ack.get("primary_source_commit", "unknown"))
    primary_pid = str(ack.get("primary_pid", "未知"))
    error_reason = str(ack.get("error_reason", "未收到有效激活确认"))
    if status == "version-conflict":
        message = (
            "另一版本的 StockWatcher 正在运行。\n"
            f"后台 PID：{primary_pid}\n路径：{primary_path}\nCommit：{primary_commit}\n"
            "请先显示旧窗口或退出旧版本后再启动。"
        )
    else:
        message = (
            "StockWatcher 后台实例无法恢复窗口。\n"
            f"后台 PID：{primary_pid}\n路径：{primary_path}\nCommit：{primary_commit}\n"
            f"原因：{error_reason}\n日志：{log_path}"
        )
    if sys.platform == "darwin":
        try:
            subprocess.Popen(
                [
                    "/usr/bin/osascript",
                    "-e",
                    "on run argv\n"
                    "display dialog item 1 of argv with title \"StockWatcher 启动提示\" "
                    "buttons {\"好\"}\n"
                    "end run",
                    message,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(run())
