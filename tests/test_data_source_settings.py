from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
)

from stock_watcher.config import DataSourceMode, DataSourceSettings  # noqa: E402
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
from stock_watcher.ui import data_source_status  # noqa: E402
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
