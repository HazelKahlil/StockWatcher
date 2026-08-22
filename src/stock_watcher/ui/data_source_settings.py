from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock, Thread

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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
from stock_watcher.providers.tushare.capabilities import (
    CapabilityCheckCoordinator,
    ProviderCapability,
    ProviderCapabilityStatus,
    aggregate_capability_status,
)
from stock_watcher.providers.tushare.rate_limit import ApplicationRequestBudget
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
    LightweightCredentialTester,
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
    request_budget: ApplicationRequestBudget | None = None
    capability_checks: CapabilityCheckCoordinator | None = None
    _pending: dict[str, PendingCredential] = field(default_factory=dict)
    _last_results: dict[str, CredentialTestResult] = field(default_factory=dict)
    _test_lock: Lock = field(default_factory=Lock)
    _pending_state_lock: Lock = field(default_factory=Lock)
    _pending_epoch: int = 0

    def __post_init__(self) -> None:
        if self.repository is not None:
            self.settings = self.repository.load()
        if self.request_budget is not None and isinstance(
            self.tester, LightweightCredentialTester
        ):
            self.tester.request_budget = self.request_budget

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
            reference = self.reference(name)
            if isinstance(self.store, KeyringCredentialStore):
                cached, secret = self.store.get_cached(reference)
                return cached and bool(secret)
            return bool(self.store.get(reference))
        except Exception:
            return False

    def credential_presence(self, name: str) -> str:
        """Read credential presence without prompting the native Keychain."""
        try:
            reference = self.reference(name)
            if isinstance(self.store, KeyringCredentialStore):
                cached, secret = self.store.get_cached(reference)
                if not cached:
                    return "unknown"
                return "present" if secret else "missing"
            return "present" if self.store.get(reference) else "missing"
        except Exception:
            return "error"

    @property
    def credential_storage_label(self) -> str:
        label = getattr(self.store, "storage_label", None)
        return label if isinstance(label, str) and label else "系统安全存储"

    def credential_storage_status(self) -> str:
        status = getattr(self.store, "backend_status", None)
        if not callable(status):
            return f"{self.credential_storage_label}已就绪"
        if isinstance(self.store, KeyringCredentialStore):
            # Status polling must never enter SecItemCopyMatching.  The actual
            # backend validation happens only during an explicit save/delete.
            cached, _ = self.store.get_cached(PRIMARY_CREDENTIAL)
            return (
                f"{self.credential_storage_label}已就绪"
                if cached
                else f"{self.credential_storage_label}等待后台检测"
            )
        try:
            status()
        except Exception:
            return f"{self.credential_storage_label}不可用"
        return f"{self.credential_storage_label}已就绪"

    def test_candidate(
        self,
        name: str,
        secret: str,
        *,
        base_url: str,
        use_system_proxy: bool,
    ) -> CredentialTestResult:
        if not self._test_lock.acquire(blocking=False):
            return CredentialTestResult(
                success=False,
                tested_at=datetime.now().astimezone(),
                status_text="基础连接正在检测，请等待当前检查完成。",
                permission_summary="当前只允许一个在途 Token 检查。",
                expires_at="未知",
                safe_reason="check_in_progress",
            )
        try:
            with self._pending_state_lock:
                pending_epoch = self._pending_epoch
            return self._test_candidate_locked(
                name,
                secret,
                base_url=base_url,
                use_system_proxy=use_system_proxy,
                pending_epoch=pending_epoch,
            )
        finally:
            self._test_lock.release()

    def _test_candidate_locked(
        self,
        name: str,
        secret: str,
        *,
        base_url: str,
        use_system_proxy: bool,
        pending_epoch: int,
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
            self._stage_test_result(
                name,
                secret,
                result,
                self.profile(name),
                pending_epoch=pending_epoch,
            )
            return result
        profile = HttpProfile.model_validate(
            {
                **self.profile(name).model_dump(mode="json"),
                "base_url": base_url,
                "use_system_proxy": use_system_proxy,
            }
        )
        result = self.tester.test(profile, secret)
        self._stage_test_result(
            name,
            secret,
            result,
            profile,
            pending_epoch=pending_epoch,
        )
        return result

    def _stage_test_result(
        self,
        name: str,
        secret: str,
        result: CredentialTestResult,
        profile: HttpProfile,
        *,
        pending_epoch: int,
    ) -> None:
        with self._pending_state_lock:
            if pending_epoch != self._pending_epoch:
                return
            self._last_results[name] = result
            if result.success:
                self._pending[name] = PendingCredential(
                    secret=secret,
                    result=result,
                    profile=profile,
                )
            else:
                pending = self._pending.pop(name, None)
                if pending is not None:
                    pending.secret = ""

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
        if name == "primary" and self.capability_checks is not None:
            # The new Token is now atomically stored.  All remaining checks are
            # deliberately asynchronous and independent of this save result.
            self.capability_checks.reset()
            self.capability_checks.start_background()
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
        with self._pending_state_lock:
            pending = self._pending.pop(name, None)
            if pending is not None:
                pending.secret = ""
        try:
            removed = self.store.delete(self.reference(name))
        except Exception:
            return False
        if name == "primary" and self.capability_checks is not None:
            self.capability_checks.reset()
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
        with self._pending_state_lock:
            self._pending_epoch += 1
            for pending in self._pending.values():
                pending.secret = ""
            self._pending.clear()

    def capability_statuses(
        self,
    ) -> dict[ProviderCapability, ProviderCapabilityStatus]:
        if self.capability_checks is None:
            return {}
        return self.capability_checks.statuses()

    def start_capability_checks(self, *, retry_failed: bool = False) -> bool:
        if self.capability_checks is None or not self.credential_present("primary"):
            return False
        if retry_failed:
            return self.capability_checks.retry_now()
        return self.capability_checks.start_background()


class _PrimaryEditor(QGroupBox):
    def __init__(
        self,
        controller: DataSourceSettingsController,
        parent: QWidget | None = None,
        *,
        show_advanced_settings: bool = True,
    ) -> None:
        super().__init__("Tushare 数据接口", parent)
        self.setObjectName("dataSourceEditor")
        self.controller = controller
        self._test_generation = 0
        self._test_result_lock = Lock()
        self._test_result: tuple[int, CredentialTestResult] | None = None
        self._test_poll_timer = QTimer(self)
        self._test_poll_timer.setInterval(25)
        self._test_poll_timer.timeout.connect(self._poll_candidate_test)
        profile = controller.profile("primary")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 26, 22, 20)
        layout.setSpacing(16)
        form = QFormLayout()
        # QMacStyle defaults to keeping fields at their size hint.  Without an
        # explicit policy, the Token input collapses to a short placeholder and
        # the explanatory labels are laid out as if they had a tiny column.
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        form.setObjectName("dataSourceForm")

        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret.setObjectName("tokenInput")
        self.secret.setMinimumWidth(380)
        self.secret.setMinimumHeight(42)
        self.secret.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.secret.setClearButtonEnabled(True)
        self.secret.setAccessibleName("Tushare Token")
        self.secret.setToolTip("粘贴 Token 后，点击“测试并保存”。")
        self.secret.setPlaceholderText(
            f"粘贴 Token（保存到{controller.credential_storage_label}）"
        )
        token_field = QWidget()
        token_layout = QVBoxLayout(token_field)
        token_layout.setContentsMargins(0, 0, 0, 0)
        token_layout.setSpacing(6)
        token_layout.addWidget(self.secret)
        self.token_hint = QLabel(
            "粘贴 Token 后点击“测试并保存”。保存后可重新检测或清除。"
        )
        self.token_hint.setObjectName("tokenInputHint")
        self.token_hint.setWordWrap(True)
        token_layout.addWidget(self.token_hint)

        self.status = QLabel(
            "已设置，可测试或更换"
            if controller.credential_present("primary")
            else "尚未设置"
        )
        self.status.setObjectName("dataSourceStatus")
        self.status.setWordWrap(True)
        self.last_test = QLabel("尚未检测")
        self.last_test.setObjectName("dataSourceValue")
        self.credential_storage = QLabel(controller.credential_storage_status())
        self.credential_storage.setObjectName("dataSourceValue")
        self.permission = QLabel("接口配置已内置；保存后将用于实时行情、历史和板块。")
        self.permission.setObjectName("dataSourcePermission")
        self.permission.setWordWrap(True)
        self.permission.setMinimumHeight(38)
        self.permission.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.MinimumExpanding,
        )
        self.basic_capability = QLabel("等待保存 Token")
        self.realtime_capability = QLabel("等待保存 Token")
        self.sector_history_capability = QLabel("等待保存 Token")
        for label in (
            self.basic_capability,
            self.realtime_capability,
            self.sector_history_capability,
        ):
            label.setObjectName("dataSourceValue")
            label.setWordWrap(True)
            label.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Minimum,
            )
        form.addRow("Token", token_field)
        form.addRow("安全存储", self.credential_storage)
        form.addRow("当前状态", self.status)
        form.addRow("最近检测", self.last_test)
        form.addRow("基础数据", self.basic_capability)
        form.addRow("实时行情", self.realtime_capability)
        form.addRow("板块与历史", self.sector_history_capability)
        form.addRow("检测说明", self.permission)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 4, 0, 0)
        buttons.setSpacing(10)
        self.save_button = QPushButton("测试并保存")
        self.save_button.setObjectName("primaryButton")
        self.save_button.setToolTip("先测试基础连接；通过后再确认保存 Token。")
        self.recheck_button = QPushButton("重新检测")
        self.recheck_button.setObjectName("secondaryButton")
        self.recheck_button.setToolTip("保存 Token 后重新检测基础、实时、板块与历史能力。")
        self.clear_button = QPushButton("清除")
        self.clear_button.setObjectName("dangerButton")
        self.clear_button.setToolTip("清除已保存的 Token 并暂停新候选。")
        for button in (self.save_button, self.recheck_button, self.clear_button):
            button.setMinimumHeight(42)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.recheck_button)
        buttons.addStretch(1)
        buttons.addWidget(self.clear_button)
        layout.addLayout(buttons)

        self._base_url = str(profile.base_url).rstrip("/")
        self._use_system_proxy = profile.use_system_proxy
        self.address: QLineEdit | None = None
        self.proxy: QComboBox | None = None
        if show_advanced_settings:
            self.advanced = QGroupBox("高级设置（接口地址）")
            self.advanced.setCheckable(True)
            self.advanced.setChecked(False)
            advanced_form = QFormLayout(self.advanced)
            self.address = QLineEdit(self._base_url)
            self.address.setReadOnly(True)
            self.proxy = QComboBox()
            self.proxy.addItem("不使用系统代理", False)
            self.proxy.addItem("使用系统代理", True)
            self.proxy.setCurrentIndex(1 if self._use_system_proxy else 0)
            realtime = QLabel("https://realtime.stockai888.top")
            realtime.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            advanced_form.addRow("普通/历史/板块", self.address)
            advanced_form.addRow("原生实时校验", realtime)
            advanced_form.addRow("代理", self.proxy)
            self._set_advanced_visible(False)
            self.advanced.toggled.connect(self._set_advanced_visible)
            layout.addWidget(self.advanced)

        self.save_button.clicked.connect(self._test_and_save)
        self.recheck_button.clicked.connect(self._recheck)
        self.clear_button.clicked.connect(self._clear)
        self._capability_timer = QTimer(self)
        self._capability_timer.setInterval(500)
        self._capability_timer.timeout.connect(self._refresh_capabilities)
        self._capability_timer.start()
        self._refresh_capabilities()

    def _set_advanced_visible(self, checked: bool) -> None:
        for child in self.advanced.findChildren(QWidget):
            if child is not self.advanced:
                child.setVisible(checked)
        self.advanced.setMaximumHeight(16777215 if checked else 28)

    def _test_and_save(self) -> None:
        secret = self.secret.text()
        base_url = (
            self.address.text().strip()
            if self.address is not None
            else self._base_url
        )
        use_system_proxy = (
            bool(self.proxy.currentData())
            if self.proxy is not None
            else self._use_system_proxy
        )
        if not secret:
            result = self.controller.test_candidate(
                "primary",
                secret,
                base_url=base_url,
                use_system_proxy=use_system_proxy,
            )
            self._apply_candidate_test_result(result)
            return
        self._test_generation += 1
        generation = self._test_generation
        with self._test_result_lock:
            self._test_result = None
        self._set_test_busy(True)
        self.status.setText("正在后台测试基础连接；窗口仍可关闭。")
        Thread(
            target=self._run_candidate_test,
            args=(generation, secret, base_url, use_system_proxy),
            name="stockwatcher-token-test",
            daemon=True,
        ).start()
        self._test_poll_timer.start()

    def _run_candidate_test(
        self,
        generation: int,
        secret: str,
        base_url: str,
        use_system_proxy: bool,
    ) -> None:
        try:
            result = self.controller.test_candidate(
                "primary",
                secret,
                base_url=base_url,
                use_system_proxy=use_system_proxy,
            )
        except Exception as error:  # noqa: BLE001 - publish only the safe class name
            result = CredentialTestResult(
                success=False,
                tested_at=datetime.now().astimezone(),
                status_text="基础连接测试异常，当前 Token 未被替换。",
                permission_summary=f"安全存储或网络检查失败：{type(error).__name__}",
                expires_at="未知",
                safe_reason="business_error",
            )
        with self._test_result_lock:
            if generation == self._test_generation:
                self._test_result = (generation, result)

    def _poll_candidate_test(self) -> None:
        with self._test_result_lock:
            value = self._test_result
            self._test_result = None
        if value is None:
            return
        generation, result = value
        if generation != self._test_generation:
            return
        self._test_poll_timer.stop()
        self._set_test_busy(False)
        self._apply_candidate_test_result(result)

    def _apply_candidate_test_result(self, result: CredentialTestResult) -> None:
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
                self.status.setText("Token 已保存；正在后台分项检测并重新预热数据")
                self.controller.start_capability_checks()
                self._refresh_capabilities()
            elif answer == QMessageBox.StandardButton.Yes:
                self.status.setText("保存失败；原 Token 保持不变")
        self._set_test_busy(False)

    def _set_test_busy(self, busy: bool) -> None:
        self.save_button.setEnabled(not busy)
        self.secret.setEnabled(not busy)
        if busy:
            self.recheck_button.setEnabled(False)
            self.clear_button.setEnabled(False)
        else:
            self._refresh_capabilities()

    def cancel_pending_test(self) -> None:
        self._test_generation += 1
        self._test_poll_timer.stop()
        with self._test_result_lock:
            self._test_result = None
        self._set_test_busy(False)

    def _recheck(self) -> None:
        if not self.controller.credential_present("primary"):
            self.status.setText("请先测试并保存 Token。")
            return
        started = self.controller.start_capability_checks(retry_failed=True)
        self.status.setText("正在后台分项检测。" if started else "检测正在进行或等待限流恢复。")
        self._refresh_capabilities()

    def _refresh_capabilities(self) -> None:
        self.credential_storage.setText(self.controller.credential_storage_status())
        has_credential = self.controller.credential_present("primary")
        self.recheck_button.setEnabled(has_credential)
        self.clear_button.setEnabled(has_credential)
        statuses = self.controller.capability_statuses()
        if not statuses:
            if has_credential:
                self.basic_capability.setText("等待后台检测")
                self.realtime_capability.setText("等待后台检测")
                self.sector_history_capability.setText("等待后台检测")
            return
        self.controller.start_capability_checks()
        self.basic_capability.setText(
            self._capability_text(
                statuses,
                (ProviderCapability.STOCK_LIST, ProviderCapability.TRADE_CALENDAR),
            )
        )
        self.realtime_capability.setText(
            self._capability_text(
                statuses,
                (
                    ProviderCapability.REALTIME_1,
                    ProviderCapability.REALTIME_100,
                    ProviderCapability.REALTIME_300,
                    ProviderCapability.REALTIME_800,
                ),
            )
        )
        self.sector_history_capability.setText(
            self._capability_text(
                statuses,
                (
                    ProviderCapability.SECTOR_CLASSIFICATION,
                    ProviderCapability.HISTORICAL_MINUTES,
                ),
            )
        )

    @staticmethod
    def _capability_text(
        statuses: dict[ProviderCapability, ProviderCapabilityStatus],
        capabilities: tuple[ProviderCapability, ...],
    ) -> str:
        selected = [statuses[capability] for capability in capabilities]
        worst = aggregate_capability_status(selected)
        detail = worst.display_text
        if worst.last_success_at is not None:
            detail += f"｜最近成功 {worst.last_success_at.strftime('%H:%M:%S')}"
        if worst.next_retry_at is not None:
            detail += f"｜预计恢复 {worst.next_retry_at.strftime('%H:%M:%S')}"
        return detail

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
                self._refresh_capabilities()
            else:
                self.status.setText("未找到 Token，或系统安全存储拒绝了清除操作")


class DataSourceSettingsDialog(QDialog):
    def __init__(
        self,
        controller: DataSourceSettingsController | None = None,
        parent: QWidget | None = None,
        *,
        platform: str = sys.platform,
    ) -> None:
        super().__init__(parent)
        self.controller = controller or DataSourceSettingsController()
        self.setWindowTitle("数据接口")
        self.setMinimumSize(680, 460)
        self.resize(760, 640)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 26, 28, 24)
        root.setSpacing(16)

        title = QLabel("数据接口")
        title.setObjectName("dialogTitle")
        description = QLabel(
            "接口配置已内置，只需一个 Tushare Token。"
            f"Token 仅保存在{self.controller.credential_storage_label}，"
            "测试失败不会替换当前 Token。"
        )
        description.setObjectName("dialogDescription")
        description.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(description)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(14)
        show_advanced_diagnostics = platform != "darwin"
        self._primary_editor = _PrimaryEditor(
            self.controller,
            show_advanced_settings=show_advanced_diagnostics,
        )
        content_layout.addWidget(self._primary_editor)

        if (
            not self.controller.credential_present("primary")
            and self.controller.credential_present("fast")
        ):
            migrate = QPushButton("迁移本机旧 Token")
            migrate.setObjectName("secondaryButton")
            migrate.setToolTip("会先测试旧 Token，成功后才复制到统一凭据位置")
            migrate.clicked.connect(self._migrate_legacy)
            content_layout.addWidget(migrate)

        self.mode: QComboBox | None = None
        if show_advanced_diagnostics:
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
            content_layout.addWidget(diagnostics)
        content_layout.addStretch()

        scroll = QScrollArea()
        scroll.setObjectName("dataSourceScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        close = QPushButton("关闭")
        close.setObjectName("secondaryButton")
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
        if self.mode is None:
            return
        mode = self.mode.currentData()
        if isinstance(mode, DataSourceMode):
            self.controller.set_mode(mode)

    def done(self, result: int) -> None:
        self._primary_editor.cancel_pending_test()
        self.controller.discard_pending()
        super().done(result)


def runtime_data_source_controller(
    on_provider_changed: Callable[[DataSourceMode], None] | None = None,
    *,
    credential_store: CredentialStore | None = None,
    request_budget: ApplicationRequestBudget | None = None,
    capability_checks: CapabilityCheckCoordinator | None = None,
) -> DataSourceSettingsController:
    paths = runtime_paths()
    repository = DataSourceConfigRepository(paths.root / "config" / "data-sources.yaml")
    settings = repository.load()
    store = credential_store or KeyringCredentialStore()
    budget = request_budget or ApplicationRequestBudget(
        settings.request_budget_interval_seconds
    )
    checks = capability_checks or CapabilityCheckCoordinator.for_profiles(
        settings.primary_profile,
        settings.native_realtime_profile,
        lambda: store.get(PRIMARY_CREDENTIAL),
        request_budget=budget,
    )
    return DataSourceSettingsController(
        settings=settings,
        store=store,
        tester=LightweightCredentialTester(request_budget=budget),
        repository=repository,
        on_provider_changed=on_provider_changed,
        request_budget=budget,
        capability_checks=checks,
    )
