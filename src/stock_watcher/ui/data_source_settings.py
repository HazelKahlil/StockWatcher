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
    PRIMARY_CREDENTIAL,
    SUPER_CREDENTIAL,
    CredentialRef,
    CredentialStore,
    KeyringCredentialStore,
)

from .data_source_status import (
    CredentialTester,
    CredentialTestResult,
    TusharePrimaryCredentialTester,
)


@dataclass(slots=True)
class PendingCredential:
    secret: str
    result: CredentialTestResult
    profile: HttpProfile


@dataclass(slots=True)
class DataSourceSettingsController:
    settings: DataSourceSettings = field(default_factory=DataSourceSettings)
    store: CredentialStore = field(default_factory=KeyringCredentialStore)
    tester: CredentialTester = field(default_factory=TusharePrimaryCredentialTester)
    repository: DataSourceConfigRepository | None = None
    on_provider_changed: Callable[[DataSourceMode], None] | None = None
    _pending: dict[str, PendingCredential] = field(default_factory=dict)
    _last_results: dict[str, CredentialTestResult] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.repository is not None:
            self.settings = self.repository.load()

    def profile(self, name: str) -> HttpProfile:
        if name == "primary":
            return self.settings.primary_profile
        if name == "super":
            return self.settings.super_profile
        if name == "fast":
            return self.settings.fast_profile
        raise ValueError("unknown data-source profile")

    @staticmethod
    def reference(name: str) -> CredentialRef:
        if name == "primary":
            return PRIMARY_CREDENTIAL
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
                status_text="请输入 Token。",
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
                secret=secret,
                result=result,
                profile=profile,
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
        profile_field = {
            "primary": "primary_profile",
            "super": "super_profile",
            "fast": "fast_profile",
        }[name]
        selected_mode = {
            "primary": DataSourceMode.TUSHARE_15000,
            "super": DataSourceMode.SUPER,
            "fast": DataSourceMode.FAST,
        }[name]
        next_settings = self.settings.model_copy(
            update={
                profile_field: pending.profile.model_copy(update={"enabled": True}),
                "mode": selected_mode,
            }
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
            self.on_provider_changed(selected_mode)
        return True

    def migrate_legacy_fast(self, *, confirmed: bool) -> bool:
        """Explicitly copy the old Fast Token to Primary after testing it."""
        if not confirmed or self.credential_present("primary"):
            return False
        try:
            legacy = self.store.get(FAST_CREDENTIAL)
        except Exception:
            return False
        if not legacy:
            return False
        profile = self.settings.primary_profile
        result = self.test_candidate(
            "primary",
            legacy,
            base_url=str(profile.base_url).rstrip("/"),
            use_system_proxy=profile.use_system_proxy,
        )
        return result.success and self.commit_candidate("primary", confirmed=True)

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


class _PrimaryEditor(QGroupBox):
    def __init__(
        self,
        controller: DataSourceSettingsController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Tushare 数据接口", parent)
        self.controller = controller
        profile = controller.profile("primary")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret.setPlaceholderText("输入 Token；只保存到 Windows 凭据管理器")
        self.status = QLabel(
            "已设置，可测试或更换"
            if controller.credential_present("primary")
            else "尚未设置"
        )
        self.status.setWordWrap(True)
        self.last_test = QLabel("尚未检测")
        self.permission = QLabel("保存后将用于实时行情、历史和板块")
        self.permission.setWordWrap(True)
        form.addRow("Token", self.secret)
        form.addRow("当前状态", self.status)
        form.addRow("最近检测", self.last_test)
        form.addRow("能力摘要", self.permission)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        self.save_button = QPushButton("测试连接并保存")
        self.clear_button = QPushButton("清除")
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.clear_button)
        layout.addLayout(buttons)

        self.advanced = QGroupBox("高级设置（接口地址）")
        self.advanced.setCheckable(True)
        self.advanced.setChecked(False)
        advanced_form = QFormLayout(self.advanced)
        self.address = QLineEdit(str(profile.base_url).rstrip("/"))
        self.address.setReadOnly(True)
        self.proxy = QComboBox()
        self.proxy.addItem("不使用系统代理", False)
        self.proxy.addItem("使用系统代理", True)
        self.proxy.setCurrentIndex(1 if profile.use_system_proxy else 0)
        realtime = QLabel("https://realtime.stockai888.top")
        realtime.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        advanced_form.addRow("普通/历史/板块", self.address)
        advanced_form.addRow("原生实时校验", realtime)
        advanced_form.addRow("代理", self.proxy)
        self._set_advanced_visible(False)
        self.advanced.toggled.connect(self._set_advanced_visible)
        layout.addWidget(self.advanced)

        self.save_button.clicked.connect(self._test_and_save)
        self.clear_button.clicked.connect(self._clear)

    def _set_advanced_visible(self, checked: bool) -> None:
        for child in self.advanced.findChildren(QWidget):
            if child is not self.advanced:
                child.setVisible(checked)
        self.advanced.setMaximumHeight(16777215 if checked else 28)

    def _test_and_save(self) -> None:
        self.save_button.setEnabled(False)
        result = self.controller.test_candidate(
            "primary",
            self.secret.text(),
            base_url=self.address.text().strip(),
            use_system_proxy=bool(self.proxy.currentData()),
        )
        self.status.setText(result.status_text)
        self.last_test.setText(result.tested_at.strftime("%Y-%m-%d %H:%M:%S"))
        self.permission.setText(result.permission_summary)
        if result.success:
            answer = QMessageBox.question(
                self,
                "确认保存 Token",
                "连接测试通过。确认安全保存并重新预热实时数据吗？",
            )
            if self.controller.commit_candidate(
                "primary",
                confirmed=answer == QMessageBox.StandardButton.Yes,
            ):
                self.secret.clear()
                self.status.setText("已连接；正在重新预热数据")
            elif answer == QMessageBox.StandardButton.Yes:
                self.status.setText("保存失败；原 Token 保持不变")
        self.save_button.setEnabled(True)

    def _clear(self) -> None:
        answer = QMessageBox.question(
            self,
            "确认清除",
            "清除后会暂停新候选，直到重新设置并预热完成。确认继续吗？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            if self.controller.clear_credential("primary"):
                self.secret.clear()
                self.status.setText("Token 已清除")
            else:
                self.status.setText("未找到 Token，或系统安全存储拒绝了清除操作")


class DataSourceSettingsDialog(QDialog):
    def __init__(
        self,
        controller: DataSourceSettingsController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller or DataSourceSettingsController()
        self.setWindowTitle("数据接口")
        self.resize(680, 590)
        root = QVBoxLayout(self)

        title = QLabel("数据接口")
        title.setObjectName("dialogTitle")
        description = QLabel(
            "只需一个 Tushare Token。Token 仅保存在 Windows 凭据管理器，"
            "测试失败不会替换当前 Token。"
        )
        description.setObjectName("dialogDescription")
        description.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(description)
        root.addWidget(_PrimaryEditor(self.controller))

        if (
            not self.controller.credential_present("primary")
            and self.controller.credential_present("fast")
        ):
            migrate = QPushButton("迁移本机旧 Token")
            migrate.setToolTip("会先测试旧 Token，成功后才复制到统一凭据位置")
            migrate.clicked.connect(self._migrate_legacy)
            root.addWidget(migrate)

        diagnostics = QGroupBox("高级诊断")
        diagnostics.setCheckable(True)
        diagnostics.setChecked(False)
        diagnostic_layout = QFormLayout(diagnostics)
        self.mode = QComboBox()
        for label, value in (
            ("Tushare 数据接口", DataSourceMode.TUSHARE_15000),
            ("Replay", DataSourceMode.REPLAY),
            ("旧接口诊断", DataSourceMode.ADVANCED_DIAGNOSTIC),
            ("通达信诊断", DataSourceMode.TDX_DIAGNOSTIC),
        ):
            self.mode.addItem(label, value)
        current = self.mode.findData(self.controller.settings.mode)
        self.mode.setCurrentIndex(max(0, current))
        self.mode.currentIndexChanged.connect(self._mode_changed)
        diagnostic_layout.addRow("运行方式", self.mode)
        self.mode.setVisible(False)
        diagnostics.toggled.connect(self.mode.setVisible)
        root.addWidget(diagnostics)
        root.addStretch()

        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def _migrate_legacy(self) -> None:
        answer = QMessageBox.question(
            self,
            "迁移旧 Token",
            "将测试本机旧 Token；通过后复制为统一 Tushare Token。确认继续吗？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            ok = self.controller.migrate_legacy_fast(confirmed=True)
            QMessageBox.information(
                self,
                "迁移结果",
                "迁移成功，正在重新预热数据。" if ok else "迁移未完成，旧 Token 未被删除。",
            )

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
