from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from stock_watcher.config import (
    DataSourceConfigRepository,
    DataSourceMode,
    DataSourceSettings,
    HttpProfile,
)
from stock_watcher.paths import runtime_paths
from stock_watcher.security import (
    FAST_CREDENTIAL,
    SUPER_CREDENTIAL,
    CredentialRef,
    CredentialStore,
    KeyringCredentialStore,
)

from .data_source_status import CredentialTester, CredentialTestResult, TushareCredentialTester


@dataclass(slots=True)
class PendingCredential:
    secret: str
    result: CredentialTestResult
    profile: HttpProfile


@dataclass(slots=True)
class DataSourceSettingsController:
    settings: DataSourceSettings = field(default_factory=DataSourceSettings)
    store: CredentialStore = field(default_factory=KeyringCredentialStore)
    tester: CredentialTester = field(default_factory=TushareCredentialTester)
    repository: DataSourceConfigRepository | None = None
    on_provider_changed: Callable[[DataSourceMode], None] | None = None
    _pending: dict[str, PendingCredential] = field(default_factory=dict)
    _last_results: dict[str, CredentialTestResult] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.repository is not None:
            self.settings = self.repository.load()

    def profile(self, name: str) -> HttpProfile:
        if name == "super":
            return self.settings.super_profile
        if name == "fast":
            return self.settings.fast_profile
        raise ValueError("unknown data-source profile")

    @staticmethod
    def reference(name: str) -> CredentialRef:
        if name == "super":
            return SUPER_CREDENTIAL
        if name == "fast":
            return FAST_CREDENTIAL
        raise ValueError("unknown data-source profile")

    def credential_present(self, name: str) -> bool:
        try:
            return bool(self.store.get(self.reference(name)))
        except Exception:
            return False

    def test_candidate(
        self,
        name: str,
        secret: str,
        *,
        base_url: str,
        use_system_proxy: bool,
    ) -> CredentialTestResult:
        if not secret:
            result = CredentialTestResult(
                success=False,
                tested_at=datetime.now().astimezone(),
                status_text="请输入新凭据。",
                permission_summary="未测试",
                expires_at="未知",
                safe_reason="credential_missing",
            )
            self._last_results[name] = result
            return result
        profile = HttpProfile.model_validate(
            {
                **self.profile(name).model_dump(mode="json"),
                "base_url": base_url,
                "use_system_proxy": use_system_proxy,
            }
        )
        result = self.tester.test(profile, secret)
        self._last_results[name] = result
        if result.success:
            self._pending[name] = PendingCredential(
                secret=secret, result=result, profile=profile
            )
        else:
            self._pending.pop(name, None)
        return result

    def commit_candidate(self, name: str, *, confirmed: bool) -> bool:
        pending = self._pending.get(name)
        if pending is None or not pending.result.success or not confirmed:
            return False
        reference = self.reference(name)
        try:
            previous_secret = self.store.get(reference)
        except Exception:
            return False
        profile_field = "super_profile" if name == "super" else "fast_profile"
        next_settings = self.settings.model_copy(
            update={profile_field: pending.profile.model_copy(update={"enabled": True})}
        )
        try:
            self.store.set(reference, pending.secret)
            if self.repository is not None:
                self.repository.save(next_settings)
        except Exception:
            try:
                if previous_secret is None:
                    self.store.delete(reference)
                else:
                    self.store.set(reference, previous_secret)
            except Exception:
                pass
            return False
        self.settings = next_settings
        pending.secret = ""
        self._pending.pop(name, None)
        if self.on_provider_changed is not None:
            self.on_provider_changed(self.settings.mode)
        return True

    def clear_credential(self, name: str) -> bool:
        self._pending.pop(name, None)
        try:
            removed = self.store.delete(self.reference(name))
        except Exception:
            return False
        if self.on_provider_changed is not None:
            self.on_provider_changed(self.settings.mode)
        return removed

    def set_mode(self, mode: DataSourceMode) -> None:
        if mode is self.settings.mode:
            return
        self.settings = self.settings.model_copy(update={"mode": mode})
        if self.repository is not None:
            self.repository.save(self.settings)
        if self.on_provider_changed is not None:
            self.on_provider_changed(mode)

    def discard_pending(self) -> None:
        for pending in self._pending.values():
            pending.secret = ""
        self._pending.clear()


class _ProfileEditor(QGroupBox):
    def __init__(
        self,
        name: str,
        title: str,
        controller: DataSourceSettingsController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self.name = name
        self.controller = controller
        profile = controller.profile(name)
        layout = QFormLayout(self)

        self.address = QLineEdit(str(profile.base_url).rstrip("/"))
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret.setPlaceholderText("输入新凭据；不会显示或写入配置")
        self.proxy = QComboBox()
        self.proxy.addItem("不使用系统代理", False)
        self.proxy.addItem("使用系统代理", True)
        self.status = QLabel("已有本机凭据" if controller.credential_present(name) else "尚未设置")
        self.status.setWordWrap(True)
        self.last_test = QLabel("尚未测试")
        self.permission = QLabel("未取得权限摘要")
        self.expiry = QLabel("未知")

        buttons = QHBoxLayout()
        self.test_button = QPushButton("测试连接")
        self.save_button = QPushButton("保存并切换" if name == "super" else "保存")
        self.save_button.setEnabled(False)
        self.clear_button = QPushButton("清除本机凭据")
        buttons.addWidget(self.test_button)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.clear_button)

        layout.addRow("地址", self.address)
        layout.addRow("API Key" if name == "super" else "Token", self.secret)
        layout.addRow("代理", self.proxy)
        layout.addRow("当前状态", self.status)
        layout.addRow("最近测试", self.last_test)
        layout.addRow("权限摘要", self.permission)
        layout.addRow("到期时间", self.expiry)
        layout.addRow(buttons)

        self.test_button.clicked.connect(self._test)
        self.save_button.clicked.connect(self._save)
        self.clear_button.clicked.connect(self._clear)

    def _test(self) -> None:
        self.test_button.setEnabled(False)
        result = self.controller.test_candidate(
            self.name,
            self.secret.text(),
            base_url=self.address.text().strip(),
            use_system_proxy=bool(self.proxy.currentData()),
        )
        self.status.setText(result.status_text)
        self.last_test.setText(result.tested_at.strftime("%Y-%m-%d %H:%M:%S"))
        self.permission.setText(result.permission_summary)
        self.expiry.setText(result.expires_at)
        self.save_button.setEnabled(result.success)
        self.test_button.setEnabled(True)

    def _save(self) -> None:
        answer = QMessageBox.question(
            self,
            "确认替换凭据",
            "新凭据已测试通过。确认替换当前本机凭据并重新预热数据吗？",
        )
        committed = self.controller.commit_candidate(
            self.name, confirmed=answer == QMessageBox.StandardButton.Yes
        )
        if committed:
            self.secret.clear()
            self.save_button.setEnabled(False)
            self.status.setText("已安全保存；候选门已关闭并等待重新预热")
        elif answer == QMessageBox.StandardButton.Yes:
            self.status.setText("保存失败；旧凭据保持不变")

    def _clear(self) -> None:
        answer = QMessageBox.question(
            self,
            "确认清除",
            "清除后该接口将停止使用，且候选门保持关闭。确认继续吗？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            if self.controller.clear_credential(self.name):
                self.secret.clear()
                self.save_button.setEnabled(False)
                self.status.setText("本机凭据已清除")
            else:
                self.status.setText("未找到凭据，或系统安全存储拒绝了清除操作")


class DataSourceSettingsDialog(QDialog):
    def __init__(
        self,
        controller: DataSourceSettingsController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller or DataSourceSettingsController()
        self.setWindowTitle("数据接口")
        self.resize(720, 720)
        root = QVBoxLayout(self)

        title = QLabel("数据接口")
        title.setObjectName("dialogTitle")
        description = QLabel(
            "正式凭据只保存在系统安全存储。测试失败不会替换旧凭据，也不会重启应用。"
        )
        description.setObjectName("dialogDescription")
        description.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(description)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("当前使用方式"))
        self.mode = QComboBox()
        labels = (
            ("超级接口", DataSourceMode.SUPER),
            ("快速接口", DataSourceMode.FAST),
            ("智能路由", DataSourceMode.SMART),
            ("Mock / Replay", DataSourceMode.REPLAY),
            ("通达信诊断模式", DataSourceMode.TDX_DIAGNOSTIC),
        )
        for label, value in labels:
            self.mode.addItem(label, value)
        current = self.mode.findData(self.controller.settings.mode)
        self.mode.setCurrentIndex(max(0, current))
        self.mode.currentIndexChanged.connect(self._mode_changed)
        mode_row.addWidget(self.mode, 1)
        root.addLayout(mode_row)

        root.addWidget(_ProfileEditor("super", "超级接口（默认主接口）", self.controller))
        root.addWidget(_ProfileEditor("fast", "快速接口（验证后可选加速）", self.controller))
        root.addStretch()

        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def _mode_changed(self) -> None:
        mode = self.mode.currentData()
        if isinstance(mode, DataSourceMode):
            self.controller.set_mode(mode)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.controller.discard_pending()
        super().closeEvent(event)


def runtime_data_source_controller(
    on_provider_changed: Callable[[DataSourceMode], None] | None = None,
) -> DataSourceSettingsController:
    paths = runtime_paths()
    repository = DataSourceConfigRepository(paths.root / "config" / "data-sources.yaml")
    return DataSourceSettingsController(
        repository=repository,
        on_provider_changed=on_provider_changed,
    )
