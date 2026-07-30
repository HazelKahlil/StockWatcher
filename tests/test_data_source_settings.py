from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
)

from stock_watcher.config import DataSourceMode, DataSourceSettings  # noqa: E402
from stock_watcher.domain import SHANGHAI  # noqa: E402
from stock_watcher.providers.tushare import (  # noqa: E402
    ProviderError,
    ProviderFailureReason,
)
from stock_watcher.security import (  # noqa: E402
    FAST_CREDENTIAL,
    SUPER_CREDENTIAL,
    CredentialRef,
    MemoryCredentialStore,
)
from stock_watcher.storage import SQLiteStore  # noqa: E402
from stock_watcher.ui import data_source_status  # noqa: E402
from stock_watcher.ui.daily_summary import DailySummaryDialog  # noqa: E402
from stock_watcher.ui.data_source_settings import (  # noqa: E402
    DataSourceSettingsController,
    DataSourceSettingsDialog,
)
from stock_watcher.ui.data_source_status import (  # noqa: E402
    CredentialTestResult,
    TushareCredentialTester,
)
from stock_watcher.ui.main_window import MainWindow  # noqa: E402
from stock_watcher.ui.tushare_session import TushareDiagnosticSession  # noqa: E402
from stock_watcher.ui.tushare_v1_session import TushareV1Session  # noqa: E402


class NoNetworkTester:
    def test(self, profile: object, secret: str) -> CredentialTestResult:
        return CredentialTestResult(
            success=True,
            tested_at=datetime.now().astimezone(),
            status_text="通过",
            permission_summary="测试摘要",
            expires_at="未知",
        )


class RealtimeAvailableTester:
    def test(self, profile: object, secret: str) -> CredentialTestResult:
        return CredentialTestResult(
            success=True,
            tested_at=datetime.now().astimezone(),
            status_text="基础与实时接口可达",
            permission_summary="实时有数据",
            expires_at="未知",
            realtime_status="available",
            realtime_records=100,
            realtime_source_timestamp_present=True,
        )


class StaticPassRealtimeEmptyTransport:
    def execute(self, request: object) -> object:
        if getattr(request, "api_name") == "rt_k":
            raise ProviderError(ProviderFailureReason.EMPTY_DATA)
        return SimpleNamespace(http_status=200)


class RejectingCredentialStore(MemoryCredentialStore):
    reject_writes: bool = False

    def set(self, reference: CredentialRef, secret: str) -> None:
        if self.reject_writes:
            raise RuntimeError("simulated keyring failure")
        super().set(reference, secret)


def application() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def test_data_source_dialog_has_one_hidden_token_and_advanced_modes() -> None:
    app = application()
    controller = DataSourceSettingsController(
        store=MemoryCredentialStore(), tester=NoNetworkTester()
    )
    dialog = DataSourceSettingsDialog(controller, platform="win32")
    password_fields = [
        field
        for field in dialog.findChildren(QLineEdit)
        if field.echoMode() is QLineEdit.EchoMode.Password
    ]
    assert len(password_fields) == 1
    assert all(not field.text() for field in password_fields)
    built_in_addresses = [
        field
        for field in dialog.findChildren(QLineEdit)
        if field.text().startswith("https://")
    ]
    assert len(built_in_addresses) == 1
    assert built_in_addresses[0].isReadOnly()
    modes = next(
        combo
        for combo in dialog.findChildren(QComboBox)
        if combo.findData(DataSourceMode.TUSHARE_15000) >= 0
    )
    assert [modes.itemText(index) for index in range(modes.count())] == [
        "Tushare 数据接口",
        "Replay",
        "旧接口诊断",
        "通达信诊断",
    ]
    dialog.close()
    app.processEvents()


def test_macos_data_source_dialog_hides_urls_and_advanced_diagnostics() -> None:
    app = application()
    dialog = DataSourceSettingsDialog(
        DataSourceSettingsController(store=MemoryCredentialStore(), tester=NoNetworkTester()),
        platform="darwin",
    )

    copy = " ".join(label.text() for label in dialog.findChildren(QLabel))
    group_titles = " ".join(group.title() for group in dialog.findChildren(QGroupBox))

    assert not dialog.findChildren(QComboBox)
    assert "接口地址" not in copy
    assert "接口地址" not in group_titles
    assert "高级诊断" not in group_titles
    dialog.close()
    app.processEvents()


def test_data_source_dialog_keeps_token_controls_readable_and_actionable() -> None:
    app = application()
    controller = DataSourceSettingsController(
        store=MemoryCredentialStore(), tester=NoNetworkTester()
    )
    dialog = DataSourceSettingsDialog(controller, platform="darwin")
    dialog.show()
    app.processEvents()

    form = dialog.findChild(QFormLayout, "dataSourceForm")
    token_input = dialog.findChild(QLineEdit, "tokenInput")
    token_hint = dialog.findChild(QLabel, "tokenInputHint")
    save = dialog.findChild(QPushButton, "primaryButton")
    recheck = next(
        button
        for button in dialog.findChildren(QPushButton)
        if button.text() == "重新检测"
    )
    clear = dialog.findChild(QPushButton, "dangerButton")

    assert form is not None
    assert form.fieldGrowthPolicy() is QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
    assert token_input is not None
    assert token_input.minimumWidth() >= 380
    assert token_input.minimumHeight() >= 42
    assert token_input.width() >= 380
    assert token_hint is not None
    assert "测试并保存" in token_hint.text()
    assert "重新检测" in token_hint.text()
    assert save is not None and save.isEnabled()
    assert save.text() == "测试并保存"
    assert recheck is not None and not recheck.isEnabled()
    assert clear is not None and not clear.isEnabled()
    assert dialog.minimumWidth() >= 680

    save.click()
    app.processEvents()
    status = dialog.findChild(QLabel, "dataSourceStatus")
    assert status is not None
    assert status.text() == "请输入 Token。"
    dialog.close()
    app.processEvents()


def test_tushare_session_starts_without_tdx_and_keeps_candidates_closed(
    tmp_path: Path,
) -> None:
    session = TushareDiagnosticSession(tmp_path / "tushare.sqlite3")
    assert session.batch is None
    assert session.candidate_gate_label == "关闭"
    assert session.data_gate_label == "未就绪"
    assert "Tushare" in session.source_label
    assert all("TQ" not in issue for issue in session.status_issues)


def test_ordinary_v1_main_window_hides_provider_and_gate_jargon(tmp_path: Path) -> None:
    app = application()
    session = TushareV1Session(
        tmp_path / "v1.sqlite3",
        credential_store=MemoryCredentialStore(),
    )
    window = MainWindow(session)
    app.processEvents()
    visible_copy = " ".join(label.text() for label in window.findChildren(QLabel))
    menu_copy = " ".join(action.text() for action in window.menuBar().actions())
    for forbidden in ("M0", "Data Gate", "Provider", "Super", "Fast", "Native"):
        assert forbidden not in visible_copy
    assert "开发" not in menu_copy
    window.close()
    app.processEvents()


def test_v1_main_window_prioritizes_candidate_area_over_status_copy(
    tmp_path: Path,
) -> None:
    app = application()
    session = TushareV1Session(
        tmp_path / "v1.sqlite3",
        credential_store=MemoryCredentialStore(),
    )
    window = MainWindow(session)
    app.processEvents()

    summary = window.findChild(QFrame, "summaryCard")
    interrupt = window.findChild(QFrame, "interruptCard")
    cards = window.findChild(QScrollArea, "cardsScroll")
    empty = window.findChild(QLabel, "emptyState")
    manual = next(
        button
        for button in window.findChildren(QPushButton)
        if button.text() == "立即获取最新3只"
    )
    reconnect = next(
        button
        for button in window.findChildren(QPushButton)
        if button.text() == "重新检测"
    )

    assert summary is not None and summary.maximumHeight() <= 88
    assert interrupt is not None and interrupt.maximumHeight() <= 138
    assert cards is not None and cards.minimumHeight() >= 280
    assert empty is not None and empty.minimumHeight() >= 150
    assert "固定显示3只观察股票" in empty.text()
    assert "说明：" not in " ".join(label.text() for label in window.findChildren(QLabel))
    assert not manual.isHidden() and manual.objectName() == "primaryButton"
    assert not reconnect.isHidden() and reconnect.objectName() == "secondaryButton"

    window.close()
    app.processEvents()


def test_daily_summary_dialog_shows_full_market_review_copy(tmp_path: Path) -> None:
    app = application()
    now = datetime.now(SHANGHAI)
    store = SQLiteStore(tmp_path / "summary.sqlite3")
    store.record_daily_summary(
        {
            "trade_date": now.date().isoformat(),
            "generated_at": now.isoformat(),
            "alert_count": 2,
            "top_sectors": [["白酒", 8]],
            "repeated_candidates": [["样本一", 1], ["样本二", 1], ["样本三", 1]],
            "closing_performance": [
                {
                    "code": "600001.SH",
                    "name": "样本一",
                    "close_price": 10.5,
                    "change_pct": 5.0,
                    "sector": "白酒",
                }
            ],
            "fund_summary": "资金未确认，本次排序未使用资金项。",
            "health_summary": "收盘日线覆盖完整。",
            "summary_text": "全市场上涨比例55%，盘后观察Top3已形成。",
            "version": "daily-summary-market-review-v1",
        }
    )

    dialog = DailySummaryDialog(store.path)
    copy = " ".join(label.text() for label in dialog.findChildren(QLabel))

    assert "今日A股盘后回顾" in copy
    assert "今日自动提醒" in copy
    assert "强势行业" in copy
    assert "盘后观察Top3" in copy
    assert "全市场上涨比例55%" in copy
    dialog.close()
    app.processEvents()


def test_daily_summary_dialog_lists_only_recent_month_and_builds_pdf(
    tmp_path: Path,
) -> None:
    app = application()
    store = SQLiteStore(tmp_path / "summary-history.sqlite3")
    for trade_date in ("2026-07-30", "2026-07-10", "2026-06-20"):
        store.record_daily_summary(
            {
                "trade_date": trade_date,
                "generated_at": f"{trade_date}T15:30:00+08:00",
                "alert_count": 0,
                "top_sectors": [["白酒", 8]],
                "repeated_candidates": [
                    ["样本一", 1],
                    ["样本二", 1],
                    ["样本三", 1],
                ],
                "closing_performance": [
                    {
                        "code": "600001.SH",
                        "name": "样本一",
                        "close_price": 10.5,
                        "change_pct": 5.0,
                        "sector": "白酒",
                    }
                ],
                "fund_summary": "资金未确认，本次排序未使用资金项。",
                "health_summary": "收盘日线覆盖完整。",
                "summary_text": "市场整体分化，盘后观察Top3已形成。",
                "version": "daily-summary-market-review-v1",
            }
        )

    dialog = DailySummaryDialog(store.path, today=date(2026, 7, 31))
    selector = dialog.findChild(QComboBox, "reportDateSelector")

    assert selector is not None
    assert selector.count() == 2
    assert [selector.itemData(index) for index in range(selector.count())] == [
        "2026-07-30",
        "2026-07-10",
    ]
    assert store.get_daily_summary("2026-06-20") is None
    pdf = dialog._ensure_internal_pdf("2026-07-30")
    assert pdf.is_file()
    assert pdf.read_bytes().count(b"/Type /Page\n") == 3

    dialog.close()
    app.processEvents()


def test_tushare_session_checks_saved_credential_without_opening_gate(
    tmp_path: Path,
) -> None:
    store = MemoryCredentialStore()
    store.set(SUPER_CREDENTIAL, "test-only-secret")
    session = TushareDiagnosticSession(
        tmp_path / "tushare.sqlite3",
        credential_store=store,
        tester=NoNetworkTester(),
    )
    session.recover()
    assert session.connection_state.value == "已连接"
    assert session.data_gate_label == "实时不可用"
    assert session.candidate_gate_label == "关闭"
    assert session.batch is None


def test_tushare_session_reports_realtime_ready_but_keeps_m0_gate_closed(
    tmp_path: Path,
) -> None:
    store = MemoryCredentialStore()
    store.set(SUPER_CREDENTIAL, "test-only-secret")
    session = TushareDiagnosticSession(
        tmp_path / "tushare.sqlite3",
        credential_store=store,
        tester=RealtimeAvailableTester(),
    )
    session.recover()
    assert session.connection_state.value == "已连接"
    assert session.data_gate_label == "实时待 M0"
    assert session.candidate_gate_label == "关闭"
    assert session.batch is None
    assert "30 分钟 M0" in session.status_issues[0]


def test_tushare_session_uses_approved_native_realtime_route_fail_closed(
    tmp_path: Path,
) -> None:
    store = MemoryCredentialStore()
    store.set(SUPER_CREDENTIAL, "test-only-super-secret")
    store.set(FAST_CREDENTIAL, "test-only-fast-secret")
    session = TushareDiagnosticSession(
        tmp_path / "tushare.sqlite3",
        credential_store=store,
        tester=NoNetworkTester(),
        native_realtime_tester=RealtimeAvailableTester(),
    )
    session.recover()
    assert session.connection_state.value == "已连接"
    assert session.data_gate_label == "实时待 M0"
    assert session.candidate_gate_label == "关闭"
    assert session.batch is None
    assert "原生实时" in session.last_fetch_detail
    assert "停滞与恢复" in session.status_issues[0]


def test_credential_test_distinguishes_static_connection_from_empty_realtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        data_source_status,
        "SuperTransport",
        lambda *_args, **_kwargs: StaticPassRealtimeEmptyTransport(),
    )
    result = TushareCredentialTester().test(
        DataSourceSettings().super_profile,
        "test-only-secret",
    )
    assert result.success
    assert result.realtime_status == "empty_data"
    assert not result.realtime_source_timestamp_present
    assert "实时快照为空" in result.permission_summary


def test_failed_keyring_replacement_preserves_previous_credential() -> None:
    store = RejectingCredentialStore()
    store.set(SUPER_CREDENTIAL, "previous-secret")
    controller = DataSourceSettingsController(store=store, tester=NoNetworkTester())
    result = controller.test_candidate(
        "super",
        "replacement-secret",
        base_url="https://ai-tool.indevs.in",
        use_system_proxy=False,
    )
    assert result.success
    store.reject_writes = True
    assert not controller.commit_candidate("super", confirmed=True)
    assert store.get(SUPER_CREDENTIAL) == "previous-secret"
