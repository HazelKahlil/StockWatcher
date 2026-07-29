from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit  # noqa: E402

from stock_watcher.config import DataSourceSettings  # noqa: E402
from stock_watcher.providers.tushare import (  # noqa: E402
    ProviderError,
    ProviderFailureReason,
)
from stock_watcher.security import (  # noqa: E402
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
from stock_watcher.ui.tushare_session import TushareDiagnosticSession  # noqa: E402


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


def test_data_source_dialog_hides_both_credentials_and_lists_all_modes() -> None:
    app = application()
    controller = DataSourceSettingsController(
        store=MemoryCredentialStore(), tester=NoNetworkTester()
    )
    dialog = DataSourceSettingsDialog(controller)
    password_fields = [
        field
        for field in dialog.findChildren(QLineEdit)
        if field.echoMode() is QLineEdit.EchoMode.Password
    ]
    assert len(password_fields) == 2
    assert all(not field.text() for field in password_fields)
    modes = dialog.findChildren(QComboBox)[0]
    assert [modes.itemText(index) for index in range(modes.count())] == [
        "超级接口",
        "快速接口",
        "智能路由",
        "Mock / Replay",
        "通达信诊断模式",
    ]
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
