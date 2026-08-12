from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from threading import Lock, Thread, current_thread
from time import monotonic as monotonic_time
from time import sleep as sleep_seconds
from typing import cast

from PySide6.QtCore import QTimer

from stock_watcher.build_info import source_commit
from stock_watcher.config import DataSourceMode, DataSourceSettings
from stock_watcher.domain import SHANGHAI, HealthState, OutcomeSlot
from stock_watcher.engine import (
    AlertPolicy,
    AlertTrigger,
    CandidateBatch,
    DailySummaryEngine,
    FundCapability,
)
from stock_watcher.paths import (
    packaged_universe_seed_path,
    report_directory_for_database,
    universe_cache_path_for_database,
)
from stock_watcher.providers.tushare import Tushare15000Provider, TushareSdkProTransport
from stock_watcher.providers.tushare.capabilities import (
    CapabilityCheckCoordinator,
    ProviderCapability,
    ProviderCapabilityState,
    ProviderCapabilityStatus,
)
from stock_watcher.providers.tushare.capability_router import CapabilityRouter
from stock_watcher.providers.tushare.errors import (
    ProviderError,
    ProviderFailureReason,
)
from stock_watcher.providers.tushare.native_realtime_transport import (
    NativeRealtimeTransport,
)
from stock_watcher.providers.tushare.provider import TushareProvider
from stock_watcher.providers.tushare.rate_limit import ApplicationRequestBudget
from stock_watcher.providers.tushare.super_transport import SuperTransport
from stock_watcher.runtime import (
    AutomationPlanner,
    AutomationTaskSpec,
    AutomationTaskState,
    AutomationTaskType,
    CandidateOutcomeTracker,
    DataHealthConfig,
    DataHealthTracker,
    FullMarketScanCoordinator,
    MarketSessionSchedule,
    OutcomeActionReport,
    RuntimeUniverse,
    RuntimeUniverseCache,
    ScanOutcome,
    TushareBootstrapLoader,
    TushareV1Runtime,
    alert_timeline_records,
    application_summary_record,
    collect_post_close_review,
    manifest_is_current,
    write_local_fallback_artifacts,
    write_pdf_manifest,
    write_post_close_report,
)
from stock_watcher.runtime.continuity import (
    analyze_scan_gaps,
    continuity_gap_summary_parts,
)
from stock_watcher.runtime.post_close_pdf import render_post_close_pdf
from stock_watcher.runtime.post_close_report_model import LocalFallbackReport
from stock_watcher.runtime.post_close_review import PostCloseDataProvider
from stock_watcher.security import (
    FAST_CREDENTIAL,
    PRIMARY_CREDENTIAL,
    SUPER_CREDENTIAL,
    CredentialStore,
    KeyringCredentialStore,
)
from stock_watcher.storage import SQLiteStore

from .connection_state import ConnectionState as TqConnectionState


@dataclass(frozen=True, slots=True)
class PendingUiAlert:
    title: str
    subtitle: str
    trigger_type: str


RuntimeFactory = Callable[
    [DataSourceSettings, CredentialStore],
    tuple[TushareV1Runtime, Tushare15000Provider],
]


class TushareV1Session:
    """Desktop session that continuously produces real stable Top3."""

    source_label = "A股全市场实时观察"
    phase_label = "非交易时段"
    app_badge = "Desktop V1"
    window_title = "StockWatcher · 当前观察"
    is_replay = False
    supports_manual_fetch = True
    auto_check_interval_seconds = 10
    connection_name = "数据接口"
    reconnect_label = "重新检测"
    manual_fetch_label = "立即获取最新3只"
    manual_fetch_timeout_seconds = 60.0
    footer_label = "只读观察 · 不连接交易账户 · 资金未确认不阻塞候选"
    advanced_diagnostics = False

    def __init__(
        self,
        store_path: Path,
        *,
        credential_store: CredentialStore | None = None,
        settings: DataSourceSettings | None = None,
        runtime_factory: RuntimeFactory | None = None,
        capability_checks: CapabilityCheckCoordinator | None = None,
        post_close_fallback_provider: PostCloseDataProvider | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = SQLiteStore(store_path)
        self.store.initialize()
        self.credential_store = credential_store or KeyringCredentialStore()
        self.settings = settings or DataSourceSettings()
        self._credential_state = "unknown"
        self._credential_error: str | None = None
        self._primary_secret_snapshot: str | None = None
        self._legacy_credential_present = False
        self._credential_refresh_lock = Lock()
        self._credential_refresh_in_flight = False
        self._credential_refresh_generation = 0
        self._credential_state_result: tuple[int, str, bool, bool, str | None] | None = None
        self._credential_poll_timer: QTimer | None = None
        self._credential_callback: Callable[[], None] | None = None
        self._request_budget = ApplicationRequestBudget(
            self.settings.request_budget_interval_seconds
        )
        self._universe_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="stockwatcher-universe",
        )
        self._universe_future: Future[RuntimeUniverse] | None = None
        self._universe_future_runtime: TushareV1Runtime | None = None
        self._universe_retry_at: datetime | None = None
        self._universe_refresh_issue: str | None = None
        self._outcome_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="stockwatcher-outcomes",
        )
        self._outcome_tracker: CandidateOutcomeTracker | None = None
        self._outcome_futures: set[Future[OutcomeActionReport]] = set()
        self._outcome_future_lock = Lock()
        self._outcome_issue: str | None = None
        self._outcome_initial_backfill_submitted = False
        self._outcome_fallback_submitted: set[str] = set()
        self._manual_started_monotonic: float | None = None
        self._manual_scan_round = 0
        self._capability_checks_required = runtime_factory is None
        self.capability_checks = capability_checks
        if self.capability_checks is None and self._capability_checks_required:
            self.capability_checks = CapabilityCheckCoordinator.for_profiles(
                self.settings.primary_profile,
                self.settings.native_realtime_profile,
                self._primary_secret,
                request_budget=self._request_budget,
            )
        if runtime_factory is None:

            def budgeted_runtime_factory(
                current_settings: DataSourceSettings,
                store: CredentialStore,
            ) -> tuple[TushareV1Runtime, Tushare15000Provider]:
                return _runtime_factory(
                    current_settings,
                    store,
                    request_budget=self._request_budget,
                    universe_cache_path=universe_cache_path_for_database(self.store.path),
                )

            self._runtime_factory: RuntimeFactory = budgeted_runtime_factory
        else:
            self._runtime_factory = runtime_factory
        self._post_close_fallback_provider = post_close_fallback_provider
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self._schedule = MarketSessionSchedule()
        self._automation = AutomationPlanner()
        self._alert_policy = AlertPolicy()
        self._runtime: TushareV1Runtime | None = None
        self._provider: Tushare15000Provider | None = None
        self._prepared_date: date | None = None
        self._summary_date: str | None = None
        self._summary_retry_at: datetime | None = None
        self._summary_issue: str | None = None
        self._history_pruned_date: date | None = None
        self._history_prune_issue: str | None = None
        self.batch: CandidateBatch | None = None
        self.pending_alert: PendingUiAlert | None = None
        self.state = HealthState.WARMING
        self.health_detail = "正在等待数据接口和交易时段。"
        self.connection_state = TqConnectionState.CHECKING
        self.connection_detail = "正在检查 Tushare Token。"
        self.data_gate_label = "正在准备"
        self.candidate_gate_label = "准备中"
        self.last_connection_check: datetime | None = None
        self.last_fetch_at: datetime | None = None
        self.last_fetch_detail = "尚未完成实时扫描。"
        self.status_issues: tuple[str, ...] = ()
        self._failure_active = False
        self._platform_recovery_lock = Lock()
        self._platform_recovery_reason: str | None = None
        self._network_interrupted = False
        self._runtime_session_id = uuid.uuid4().hex
        self._runtime_session_active = False
        self._active_scan_attempt_id: str | None = None
        self._runtime_audit_issue: str | None = None
        recovery = getattr(self.store, "last_recovery", None)
        if recovery:
            self._runtime_audit_issue = f"db-recovery:{recovery.get('source_backup', 'unknown')}"
        self.last_scan_succeeded_at: datetime | None = None
        self._recovery_round = 0
        self._stall_threshold_seconds = 90.0
        self.app_badge = "Mac V1" if sys.platform == "darwin" else "Windows V1"
        self._alert_client_platform = (
            "macos-desktop" if sys.platform == "darwin" else "windows-desktop"
        )
        self._start_runtime_session()
        if not isinstance(self.credential_store, KeyringCredentialStore):
            # In-memory stores are deterministic test/local stores; keep their
            # existing synchronous behavior while native Keychain reads stay
            # off the GUI startup path.
            self._refresh_credential_state()

    def provider_changed(self, mode: DataSourceMode) -> None:
        self.settings = self.settings.model_copy(update={"mode": mode})
        if self._universe_future is not None:
            self._universe_future.cancel()
        self._universe_future = None
        self._universe_future_runtime = None
        self._universe_retry_at = None
        self._universe_refresh_issue = None
        self._runtime = None
        self._provider = None
        self._outcome_tracker = None
        self._outcome_initial_backfill_submitted = False
        self._outcome_fallback_submitted.clear()
        self._prepared_date = None
        self.pending_alert = None
        self._failure_active = False
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.data_gate_label = "重新预热"
        self.candidate_gate_label = "暂停新候选"
        self.health_detail = "Token或运行方式已变化，旧实时基线已清空。"
        self._refresh_credential_state()

    @property
    def requires_data_source_setup(self) -> bool:
        """Open setup only after the asynchronous native credential read finishes."""
        if self.settings.mode is not DataSourceMode.TUSHARE_15000:
            return False
        if isinstance(self.credential_store, KeyringCredentialStore):
            return self._credential_state == "missing"
        # MemoryCredentialStore is synchronous and is used by deterministic
        # local/replay tests; preserve its historical live presence semantics.
        return not self._primary_present()

    @property
    def credential_state_ready(self) -> bool:
        """Whether startup credential I/O has completed."""
        return self._credential_state in {"present", "missing", "error"}

    @property
    def credential_state(self) -> str:
        """Return the credential status without entering the native backend."""
        return self._credential_state

    @property
    def credential_error(self) -> str | None:
        """Return only a safe exception class name for diagnostics."""
        return self._credential_error

    def refresh_credential_state_async(self, callback: Callable[[], None] | None = None) -> None:
        """Read native Keychain off the GUI thread and publish a memory snapshot."""
        if not isinstance(self.credential_store, KeyringCredentialStore):
            self._refresh_credential_state()
            if callback is not None:
                callback()
            return

        with self._credential_refresh_lock:
            if self._credential_refresh_in_flight:
                if callback is not None:
                    self._credential_callback = callback
                return
            self._credential_refresh_in_flight = True
            self._credential_refresh_generation += 1
            generation = self._credential_refresh_generation
            self._credential_state_result = None
            self._credential_callback = callback
            poll_timer = self._credential_poll_timer
            if poll_timer is None:
                poll_timer = QTimer()
                poll_timer.setInterval(10)
                poll_timer.timeout.connect(self._poll_credential_state)
                self._credential_poll_timer = poll_timer
            poll_timer.start()

        def read() -> None:
            primary_present = False
            legacy_present = False
            state = "missing"
            error_name: str | None = None
            try:
                primary = self.credential_store.get(PRIMARY_CREDENTIAL)
                legacy = None if primary else self.credential_store.get(FAST_CREDENTIAL)
            except Exception as error:
                state = "error"
                error_name = type(error).__name__
            else:
                primary_present = bool(primary)
                legacy_present = bool(legacy)
                # A legacy value still requires explicit migration; it cannot
                # silently become the production primary credential.
                state = "present" if primary_present else "missing"
            with self._credential_refresh_lock:
                if generation != self._credential_refresh_generation:
                    return
                self._credential_state_result = (
                    generation,
                    state,
                    primary_present,
                    legacy_present,
                    error_name,
                )

        Thread(target=read, name="stockwatcher-keychain", daemon=True).start()

    def _poll_credential_state(self) -> None:
        with self._credential_refresh_lock:
            result = self._credential_state_result
            if result is None:
                return
            self._credential_state_result = None
            poll_timer = self._credential_poll_timer
            if poll_timer is not None:
                poll_timer.stop()
        generation, state, primary_present, legacy_present, error_name = result
        self._publish_credential_state(
            generation,
            state,
            primary_present=primary_present,
            legacy_present=legacy_present,
            error_name=error_name,
        )

    def stop(self) -> None:
        self.state = HealthState.STOPPED
        self.data_gate_label = "数据中断"
        self.candidate_gate_label = "保留上次结果"
        self.health_detail = "已暂停新候选。"

    def warm_and_recover(self) -> None:
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.data_gate_label = "正在检测"
        self.candidate_gate_label = "准备中"

    def begin_platform_recovery(self, reason: str) -> None:
        """Request a fresh three-round baseline after macOS wake or reconnect."""
        with self._platform_recovery_lock:
            self._platform_recovery_reason = reason
            self._network_interrupted = False
        self._recovery_round = 0
        self.record_platform_event(
            "recovering_network",
            detail={"reason": reason},
        )
        self._cancel_in_flight_scan()
        if self._active_scan_attempt_id is not None:
            self._finish_scan_attempt(
                self._active_scan_attempt_id,
                completed_at=_shanghai(self._clock()),
                state="sleep_interrupted",
                detail="跨睡眠/恢复的扫描被作废",
            )
        self.pending_alert = None
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.data_gate_label = "重新预热"
        self.candidate_gate_label = "暂停新候选"
        self.connection_detail = reason
        self.health_detail = reason
        self.status_issues = ("旧实时基线已清理；需连续3轮新鲜完整数据后恢复。",)

    def mark_network_interrupted(self, reason: str) -> None:
        """Fail closed on a platform-reported network interruption."""
        with self._platform_recovery_lock:
            self._network_interrupted = True
        self.record_platform_event(
            "recovering_network",
            detail={"reason": reason},
        )
        self._cancel_in_flight_scan()
        self.pending_alert = None
        self.state = HealthState.STOPPED
        self.connection_state = TqConnectionState.DISCONNECTED
        self.data_gate_label = "数据中断"
        self.candidate_gate_label = "保留上次结果" if self.batch else "无新结果"
        self.connection_detail = reason
        self.health_detail = reason
        self.status_issues = ("网络恢复后将清理旧基线并重新预热。",)

    def recover(self) -> None:
        self._run(force=False, manual_request=False)

    def begin_manual_fetch(self) -> None:
        now = _shanghai(self._clock())
        required_cycles = self._manual_required_scan_cycles()
        self.warm_and_recover()
        self.phase_label = _visible_phase(now)
        self.last_fetch_at = now
        self.last_fetch_detail = "本次获取目标在60秒内完成；当前正在启动。"
        self.data_gate_label = "开始获取"
        self.candidate_gate_label = "等待全市场扫描"
        self.connection_detail = "正在准备股票名单、行业和三日趋势。"
        self.health_detail = self.connection_detail
        self.status_issues = (
            f"准备完成后将获取 {required_cycles} 轮新鲜全市场数据；"
            "首轮只会把无滚动基线的结果标为“近”。",
        )

    def manual_fetch(self) -> None:
        started = monotonic_time()
        deadline = started + self.manual_fetch_timeout_seconds
        self._manual_started_monotonic = started
        self._manual_scan_round = 0
        try:
            while True:
                scan_ready = self._manual_scan_is_ready()
                if scan_ready:
                    self._set_manual_scan_progress(
                        self._manual_scan_round + 1,
                        deadline=deadline,
                    )
                outcome = self._run(force=True, manual_request=True)
                if outcome is not None:
                    self._manual_scan_round += 1
                if (
                    outcome is not None
                    and outcome.health is HealthState.HEALTHY
                    and self.batch is not None
                    and len(self.batch.candidates) == 3
                ):
                    if self._manual_result_ready(outcome, deadline=deadline):
                        self._publish_manual_result(outcome)
                        return
                if outcome is not None and outcome.health is HealthState.STOPPED:
                    return
                if not self._manual_should_wait():
                    return
                if monotonic_time() >= deadline:
                    self._set_manual_timeout()
                    return
                sleep_seconds(0.2)
        finally:
            self._manual_started_monotonic = None

    def _manual_result_ready(
        self,
        outcome: ScanOutcome,
        *,
        deadline: float,
    ) -> bool:
        audit = outcome.selection_audit
        if audit is not None and audit.display_velocity_ready:
            return True
        # A single click is allowed to perform several bounded scans.  This
        # avoids returning a cold-start supplement as if it were the same
        # confidence as a warmed ranking, while preserving the 60-second UI
        # contract when a real minute baseline is not yet available.
        if self._manual_scan_round >= self._manual_required_scan_cycles():
            return True
        return monotonic_time() >= deadline

    def _publish_manual_result(self, outcome: ScanOutcome) -> None:
        if self.batch is None or len(self.batch.candidates) != 3:
            return
        snapshot_id = self.store.record_batch(self.batch)
        audit = outcome.selection_audit
        confirmed = bool(audit is None or audit.display_velocity_ready)
        if confirmed:
            subtitle = "本轮整体偏弱" if self.batch.overall_weak else "正式确认Top3"
        else:
            subtitle = "即时预览｜涨速尚未完全确认"
        if self.pending_alert is None:
            self.pending_alert = PendingUiAlert(
                title="当前最新3只",
                subtitle=subtitle,
                trigger_type="manual",
            )
        # Manual observations are deliberately retained even when no alert
        # event references them.  The history pruning fix keeps this snapshot
        # for the full 30-day audit window.
        self.last_fetch_detail = f"本次已保存观察 #{snapshot_id}；" + (
            "涨速窗口已就绪。" if confirmed else "稍后将由持续扫描自动确认。"
        )

    def consume_pending_alert(self) -> PendingUiAlert | None:
        pending = self.pending_alert
        self.pending_alert = None
        return pending

    def _run(
        self,
        *,
        force: bool,
        manual_request: bool,
    ) -> ScanOutcome | None:
        now = _shanghai(self._clock())
        self._submit_due_outcome_fallbacks(now)
        self._prune_history_if_due(now)
        self._detect_scan_stall(now)
        self.phase_label = _visible_phase(now)
        due_tasks = self._prepare_automation_tasks(now)
        summary_task = next(
            (spec for spec in due_tasks if spec.task_type is AutomationTaskType.SUMMARY_1530),
            None,
        )
        fixed_task = next(
            (
                spec
                for spec in due_tasks
                if spec.task_type in {AutomationTaskType.FIXED_0945, AutomationTaskType.FIXED_1445}
            ),
            None,
        )
        forced_fixed = _alert_trigger_for_task(fixed_task)
        requested_trigger = (
            fixed_task.task_type.value
            if fixed_task is not None
            else ("manual" if manual_request else "automatic")
        )
        if self._is_network_interrupted():
            # Qt's reachability signal already placed the UI in STOPPED.  The
            # scheduled worker must not probe or scan again until a positive
            # recovery signal has cleared this external interruption.
            self._record_scan_skip(
                now,
                trigger_type=requested_trigger,
                detail="平台报告网络中断；未发起实时请求。",
                health=HealthState.STOPPED,
                task_key=fixed_task.task_key if fixed_task else None,
            )
            return None
        self.last_connection_check = now
        if self.settings.mode is not DataSourceMode.TUSHARE_15000:
            self.state = HealthState.WARMING
            self.connection_state = TqConnectionState.NOT_APPLICABLE
            self.connection_detail = "已选择高级诊断；重启 StockWatcher 后打开。"
            self.data_gate_label = "等待重启"
            self.candidate_gate_label = "保留上次结果" if self.batch else "暂停"
            self.health_detail = self.connection_detail
            self.status_issues = (self.connection_detail,)
            self._fail_fixed_task(fixed_task, now, self.connection_detail)
            self._record_scan_skip(
                now,
                trigger_type=requested_trigger,
                detail=self.connection_detail,
                health=HealthState.WARMING,
                task_key=fixed_task.task_key if fixed_task else None,
            )
            return None
        if not self.credential_state_ready:
            self._set_credential_pending()
            self._record_scan_skip(
                now,
                trigger_type=requested_trigger,
                detail=self.connection_detail,
                health=HealthState.WARMING,
                task_key=fixed_task.task_key if fixed_task else None,
            )
            return None
        if self._credential_state == "error":
            self._set_credential_error()
            self._fail_fixed_task(fixed_task, now, self.connection_detail)
            self._record_scan_skip(
                now,
                trigger_type=requested_trigger,
                detail=self.connection_detail,
                health=HealthState.WARMING,
                task_key=fixed_task.task_key if fixed_task else None,
            )
            return None
        secret_present = self._primary_present()
        if not secret_present:
            if summary_task is not None:
                self._execute_summary_task(now, summary_task)
            self._set_missing_credential()
            self._fail_fixed_task(fixed_task, now, self.connection_detail)
            self._record_scan_skip(
                now,
                trigger_type=requested_trigger,
                detail=self.connection_detail,
                health=HealthState.WARMING,
                task_key=fixed_task.task_key if fixed_task else None,
            )
            return None
        self._apply_pending_platform_recovery()
        if self._runtime is None or self._provider is None:
            self._runtime, self._provider = self._runtime_factory(
                self.settings,
                self.credential_store,
            )
            if _supports_outcome_provider(self._provider):
                self._outcome_tracker = CandidateOutcomeTracker(
                    self.store,
                    self._provider,
                    max_realtime_age_seconds=max(
                        120.0,
                        float(self.settings.source_fresh_seconds),
                    ),
                )
            if (
                self._runtime.universe is not None
                and now.date() in self._runtime.universe.open_dates
            ):
                self._prepared_date = now.date()
        assert self._runtime is not None
        if summary_task is not None:
            self._execute_summary_task(now, summary_task)
        self._poll_universe_refresh(now)
        if (
            self._runtime.universe is not None
            and not self._runtime.universe.concept_loaded
            and (self._universe_retry_at is None or now >= self._universe_retry_at)
        ):
            # Concept membership is optional for realtime continuity but is a
            # real V1 selection capability. Retry it in the low-priority static
            # lane while the last verified industry context keeps scans alive.
            self._start_universe_refresh(now)
        if self.capability_checks is not None and self._runtime.universe is not None:
            self.capability_checks.seed_realtime_codes(
                security.code for security in self._runtime.universe.securities
            )
        universe_current = _runtime_universe_is_current(self._runtime, now)
        universe_usable = _runtime_universe_is_usable(self._runtime, now)
        if not universe_current:
            self._start_universe_refresh(now)
            if not universe_usable:
                self._set_universe_warming(now)
                self._fail_fixed_task(fixed_task, now, self.health_detail)
                self._record_scan_skip(
                    now,
                    trigger_type=requested_trigger,
                    detail=self.health_detail,
                    health=HealthState.WARMING,
                    task_key=fixed_task.task_key if fixed_task else None,
                )
                return None
            self.status_issues = tuple(
                dict.fromkeys(
                    (
                        *self.status_issues,
                        "基础缓存正在后台刷新；本轮使用最近一次可用行业、概念和三日数据。",
                    )
                )
            )
        if (
            self.capability_checks is not None
            and self._capability_checks_required
            and not _realtime_capabilities_ready(self.capability_checks.statuses())
        ):
            # Capability probes are diagnostics, not a production data gate.
            # The previous build refused to scan until 1/100/300/800 probe
            # statuses were all green; one transient 429 could therefore erase
            # the entire trading day, including 09:45 and 14:45.  Start the
            # probes in the background and let the actual full-market scan be
            # the authoritative capability check.
            self.capability_checks.start_realtime_background()
            self.status_issues = tuple(
                dict.fromkeys(
                    (
                        *self.status_issues,
                        "实时能力后台检测中；本轮仍以真实全市场扫描作为权威验证。",
                    )
                )
            )
        if _initial_outcome_backfill_window(now):
            self._schedule_initial_outcome_backfill(now)
        open_dates = self._runtime.universe.open_dates if self._runtime.universe is not None else ()
        if (
            fixed_task is None
            and not force
            and not _session_is_trading(self._schedule, now, open_dates)
        ):
            if self.capability_checks is not None:
                self.capability_checks.start_background()
            self.state = HealthState.WARMING
            self.connection_state = TqConnectionState.CONNECTED
            self.connection_detail = "Token已配置。"
            self.data_gate_label = "非交易时段"
            self.candidate_gate_label = "上次结果" if self.batch else "等待开盘"
            self.phase_label = _visible_phase(now)
            self.health_detail = "非交易时段不发起全市场实时扫描。"
            self.status_issues = (self._summary_issue,) if self._summary_issue is not None else ()
            return None
        if fixed_task is not None:
            self._mark_task_running(fixed_task, now)
        self.last_fetch_at = now
        scan_trigger = (
            fixed_task.task_type.value
            if fixed_task is not None
            else ("manual" if manual_request else "automatic")
        )
        attempt_id = self._begin_scan_attempt(now=now, operation=scan_trigger)
        outcome = self._runtime.scan_once()
        if self._is_network_interrupted():
            # An interruption landed while the request was in flight.  Its
            # cancellation outcome must not overwrite the fail-closed state.
            self._finish_scan_attempt(
                attempt_id,
                completed_at=_shanghai(self._clock()),
                state="cancelled",
                detail="network-interrupted",
            )
            return None
        if self._apply_pending_platform_recovery():
            # A sleep/wake or network event arrived while the request was in
            # flight.  The cancelled response is intentionally not ranked,
            # persisted or allowed to trigger an old alert.
            self._finish_scan_attempt(
                attempt_id,
                completed_at=_shanghai(self._clock()),
                state="sleep_interrupted",
                detail="platform-recovery",
            )
            return None
        if self._runtime.universe is not None:
            self._prepared_date = now.date()
        self.state = outcome.health
        if outcome.failure_reason is not None:
            self._record_interruption(now, outcome.health, outcome.failure_reason)
        elif outcome.health is HealthState.HEALTHY:
            self._failure_active = False
        self.health_detail = outcome.detail
        self.last_fetch_detail = outcome.detail
        self.connection_state = (
            TqConnectionState.CONNECTED
            if outcome.health is not HealthState.STOPPED
            else TqConnectionState.DISCONNECTED
        )
        self.connection_detail = (
            "实时行情与板块数据正常。" if outcome.health is HealthState.HEALTHY else outcome.detail
        )
        if outcome.batch is not None:
            self.batch = outcome.batch
        if outcome.failure_reason == "rate_limited":
            remaining = self._request_budget.cooldown_remaining(lane="realtime")
            self.data_gate_label = "等待限流恢复"
            self.candidate_gate_label = "保留上次结果" if self.batch else "暂停新候选"
            self.connection_state = TqConnectionState.CHECKING
            self.connection_detail = "接口暂时限流，已保存Token，等待自动恢复。"
            self.health_detail = self.connection_detail
            self.status_issues = (f"预计约 {max(1, round(remaining))} 秒后从失败环节继续检测。",)
        elif outcome.health is HealthState.HEALTHY:
            self.data_gate_label = "运行正常"
            self.phase_label = _phase(now)
            if outcome.batch is None or len(outcome.batch.candidates) != 3:
                self.candidate_gate_label = "无新结果"
                self.status_issues = ("本轮实时扫描完成，但没有形成合规三只。",)
            else:
                self.candidate_gate_label = "3只观察"
                fund_issue = "资金未确认，本轮只使用价格、板块和三日趋势。"
                if (
                    self._runtime.universe is not None
                    and self._runtime.universe.fund_capability.capability
                    is FundCapability.DAILY_ONLY
                ):
                    fund_issue = "资金接口仅有日级数据，不作为盘中增强依据。"
                self.status_issues = (
                    (fund_issue,) if outcome.batch.fund_module == "unavailable" else ()
                )
        elif outcome.health is HealthState.WARMING:
            required_cycles = self._manual_required_scan_cycles()
            self.data_gate_label = "正在准备"
            self.candidate_gate_label = "保留上次结果" if self.batch else "准备中"
            self.status_issues = (f"当前需连续 {required_cycles} 轮新鲜完整数据后恢复。",)
        else:
            self.data_gate_label = "数据中断"
            self.candidate_gate_label = "保留上次结果" if self.batch else "无新结果"
            self.status_issues = ("本轮未生成新候选。",)
        completed_at = _shanghai(self._clock())
        if outcome.health is HealthState.HEALTHY:
            self.last_scan_succeeded_at = completed_at
            self._recovery_round += 1
            if self._recovery_round >= 3:
                self.record_platform_event("ready", detail={"rounds": self._recovery_round})
                self._recovery_round = 0
        attempt_state = (
            "completed"
            if outcome.health is HealthState.HEALTHY
            else ("failed" if outcome.failure_reason is not None else "completed")
        )
        self._finish_scan_attempt(
            attempt_id,
            completed_at=completed_at,
            state=attempt_state,
            detail=outcome.failure_reason or outcome.detail,
        )
        scan_run_id = self._record_scan_run(
            outcome,
            started_at=now,
            completed_at=completed_at,
            trigger_type=scan_trigger,
            task_key=fixed_task.task_key if fixed_task is not None else None,
        )
        crossed = (
            None
            if fixed_task is not None
            else self._schedule.crossed_fixed_trigger(now, completed_at)
        )
        effective_fixed = forced_fixed or crossed
        if effective_fixed is not None and outcome.health is HealthState.HEALTHY:
            self._settle_candidate_outcomes_safely(
                effective_fixed,
                outcome,
                completed_at,
            )
        snapshot_id = self._evaluate_alerts(
            completed_at,
            outcome.strong_event,
            forced_fixed=effective_fixed,
            selection_audit=outcome.selection_audit,
            scan_run_id=scan_run_id,
        )
        completed_fixed_task = fixed_task or _automation_spec_for_trigger(
            self._automation,
            crossed,
            completed_at.date(),
        )
        if completed_fixed_task is not None:
            if snapshot_id is not None:
                self._mark_task_succeeded(
                    completed_fixed_task,
                    completed_at,
                    snapshot_id=snapshot_id,
                )
            else:
                self._fail_fixed_task(
                    completed_fixed_task,
                    completed_at,
                    outcome.detail,
                    increment_attempt=False,
                )
        return outcome

    def _evaluate_alerts(
        self,
        now: datetime,
        strong_event: object | None,
        *,
        forced_fixed: AlertTrigger | None = None,
        selection_audit: object | None = None,
        scan_run_id: int | None = None,
    ) -> int | None:
        if self.batch is None or len(self.batch.candidates) != 3:
            return None
        fixed = forced_fixed or self._schedule.fixed_trigger(now)
        if fixed is not None:
            existing = next(
                (row for row in self._today_alerts(now) if row.get("trigger_type") == fixed.value),
                None,
            )
            if existing is not None:
                snapshot_id = existing.get("snapshot_id")
                return snapshot_id if isinstance(snapshot_id, int) else None
            decision = self._alert_policy.decide(self.batch, now, fixed)
            if decision.should_alert:
                title = (
                    "09:45 观察提醒" if fixed is AlertTrigger.SCHEDULED_0945 else "14:45 观察提醒"
                )
                subtitle = (
                    "数据延迟，展示上次结果"
                    if self.state is not HealthState.HEALTHY
                    else ("本轮整体偏弱" if self.batch.overall_weak else "当前最新3只")
                )
                return self._record_alert(
                    now,
                    fixed,
                    decision.reason,
                    title,
                    subtitle,
                )
            return None
        from stock_watcher.engine import StrongMovementEvent

        if not isinstance(strong_event, StrongMovementEvent):
            return None
        audit = selection_audit
        readiness = "cold"
        velocity_ready = False
        if audit is not None:
            readiness = str(getattr(audit, "warmup_state", "cold"))
            velocity_ready = bool(getattr(audit, "display_velocity_ready", False))
        if not velocity_ready:
            # A cold/warming baseline must never fire an intraday anomaly alert.
            return None
        today_intraday = [
            row
            for row in self._today_alerts(now)
            if row.get("trigger_type") == AlertTrigger.INTRADAY.value
        ]
        if len(today_intraday) >= self._alert_policy.config.daily_limit:
            return None
        cooldown_cutoff = now - self._alert_policy.config.cooldown
        for row in today_intraday:
            displayed = _parsed_datetime(row.get("displayed_at"))
            if displayed is None or displayed < cooldown_cutoff:
                continue
            if set(strong_event.triggering_codes) & _payload_codes(row.get("payload_json")):
                return None
        decision = self._alert_policy.decide(
            self.batch,
            now,
            AlertTrigger.INTRADAY,
            strong_movement=True,
            triggering_codes=strong_event.triggering_codes,
            event_strength=strong_event.strength,
        )
        if decision.should_alert:
            detail = self._build_strong_alert_detail(
                now,
                strong_event,
                audit,
                decision.reason,
                readiness,
                scan_run_id,
            )
            trigger_name = str(
                detail.get("trigger_name") or detail.get("trigger_symbol") or ""
            ).strip()
            trigger_prefix = ""
            if trigger_name:
                trigger_prefix = (
                    f"{trigger_name}等{len(strong_event.triggering_codes)}只触发｜"
                    if len(strong_event.triggering_codes) > 1
                    else f"{trigger_name}触发｜"
                )
            subtitle = trigger_prefix + (
                "个股与板块同步增强｜资金未确认"
                if strong_event.funds_unconfirmed
                else "个股、板块与资金同步增强"
            )
            return self._record_alert(
                now,
                AlertTrigger.INTRADAY,
                decision.reason,
                "盘中强异动",
                subtitle,
                detail=detail,
            )
        return None

    def _build_strong_alert_detail(
        self,
        now: datetime,
        strong_event: object,
        audit: object | None,
        decision_reason: str,
        readiness: str,
        scan_run_id: int | None,
    ) -> dict[str, object]:
        codes = tuple(getattr(strong_event, "triggering_codes", ()))
        trigger_symbol = codes[0] if codes else ""
        trigger_name = ""
        audit_rows = tuple(getattr(audit, "rows", ())) if audit is not None else ()
        row = next(
            (
                candidate
                for candidate in audit_rows
                if getattr(candidate, "code", "") == trigger_symbol
            ),
            None,
        )
        if row is not None:
            trigger_name = str(getattr(row, "name", ""))
        detail: dict[str, object] = {
            "trigger_symbol": trigger_symbol,
            "trigger_name": trigger_name,
            "trigger_time": now.isoformat(),
            "raw_rank_before": getattr(row, "raw_rank", None),
            "raw_rank_after": None,
            "velocity_1m_before": None,
            "velocity_3m_before": None,
            "velocity_5m_before": None,
            "sector_name": getattr(row, "sector", ""),
            "sector_type": getattr(row, "sector_type", ""),
            "sector_up_ratio": None,
            "sector_strong_count": None,
            "feature_readiness": readiness,
            "cooldown_decision": decision_reason,
            "source_scan_id": scan_run_id,
        }
        return {key: value for key, value in detail.items() if value is not None}

    def _today_alerts(self, now: datetime) -> list[dict[str, object]]:
        return [
            row
            for row in self.store.list_alert_history(now=now, days=1)
            if str(row.get("displayed_at", "")).startswith(now.date().isoformat())
        ]

    def _prepare_automation_tasks(
        self,
        now: datetime,
    ) -> tuple[AutomationTaskSpec, ...]:
        """Persist daily obligations and return those currently executable."""
        if now.date().weekday() >= 5:
            return ()
        for spec in self._automation.for_date(now.date()):
            self.store.ensure_automation_task(
                {
                    "task_key": spec.task_key,
                    "task_type": spec.task_type.value,
                    "trade_date": spec.trade_date.isoformat(),
                    "target_at": spec.target_at.isoformat(),
                    "deadline_at": spec.deadline_at.isoformat(),
                    "state": AutomationTaskState.PLANNED.value,
                    "updated_at": now.isoformat(),
                    "detail": "等待目标时间。",
                }
            )
        self._expire_automation_tasks(now)
        due: list[AutomationTaskSpec] = []
        for spec in self._automation.due(now):
            saved = self.store.get_automation_task(spec.task_key)
            if saved is None:
                continue
            if saved["state"] == AutomationTaskState.SUCCEEDED.value:
                continue
            due.append(spec)
        return tuple(due)

    def _expire_automation_tasks(self, now: datetime) -> None:
        for task in self.store.list_automation_tasks(now.date().isoformat()):
            if task["state"] == AutomationTaskState.SUCCEEDED.value:
                continue
            deadline = _parsed_datetime(task.get("deadline_at"))
            if deadline is None or now <= deadline:
                continue
            self.store.update_automation_task(
                str(task["task_key"]),
                state=AutomationTaskState.FAILED.value,
                updated_at=now.isoformat(),
                detail="超过产品截止时间仍未成功；保留失败证据。",
            )

    def _mark_task_running(self, spec: AutomationTaskSpec, now: datetime) -> None:
        self.store.update_automation_task(
            spec.task_key,
            state=AutomationTaskState.RUNNING.value,
            updated_at=now.isoformat(),
            detail="高优先级任务已开始。",
            increment_attempt=True,
        )

    def _mark_task_succeeded(
        self,
        spec: AutomationTaskSpec,
        now: datetime,
        *,
        snapshot_id: int | None = None,
        detail: str = "任务已完成。",
    ) -> None:
        self.store.update_automation_task(
            spec.task_key,
            state=AutomationTaskState.SUCCEEDED.value,
            updated_at=now.isoformat(),
            detail=detail,
            snapshot_id=snapshot_id,
        )

    def _fail_fixed_task(
        self,
        spec: AutomationTaskSpec | None,
        now: datetime,
        detail: str,
        *,
        increment_attempt: bool = True,
    ) -> None:
        if spec is None:
            return
        # A failed attempt remains auditable and can retry while the deadline
        # is still open.  The planner will promote it back to RUNNING on the
        # next session tick rather than silently losing the fixed obligation.
        self.store.update_automation_task(
            spec.task_key,
            state=AutomationTaskState.FAILED.value,
            updated_at=now.isoformat(),
            detail=detail,
            increment_attempt=increment_attempt,
        )
        if self._outcome_tracker is not None:
            self._submit_due_outcome_fallbacks(now)

    def check_automation_tasks(self, *, now: datetime | None = None) -> None:
        """Independent 15:30 scheduling entry, decoupled from the scan loop.

        The summary obligation must reach a terminal state even when the
        realtime scan loop is stalled, sleeping or restarting.  A late launch
        still catches up with catch_up=True.
        """
        current = _shanghai(now or self._clock())
        if current.date().weekday() >= 5:
            return
        summary_task = next(
            (
                spec
                for spec in self._prepare_automation_tasks(current)
                if spec.task_type is AutomationTaskType.SUMMARY_1530
            ),
            None,
        )
        if summary_task is None:
            return
        catch_up = current > summary_task.target_at + timedelta(minutes=1)
        self._execute_summary_task(current, summary_task, catch_up=catch_up)

    def _record_scan_run(
        self,
        outcome: ScanOutcome,
        *,
        started_at: datetime,
        completed_at: datetime,
        trigger_type: str,
        task_key: str | None,
    ) -> int:
        batch = outcome.batch
        raw = outcome.raw_batch
        source_ts = (
            batch.source_ts.isoformat()
            if batch is not None
            else (raw.source_ts.isoformat() if raw is not None else None)
        )
        audit = outcome.selection_audit
        return self.store.record_scan_run(
            {
                "started_at": started_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "trigger_type": trigger_type,
                "task_key": task_key,
                "health": outcome.health.value,
                "source_ts": source_ts,
                "coverage_ratio": outcome.coverage_ratio,
                "elapsed_seconds": outcome.elapsed_seconds,
                "source_age_seconds": outcome.source_age_seconds,
                "detail": outcome.detail,
                "raw_batch_json": raw.trace_payload() if raw is not None else None,
                "stable_batch_json": batch.trace_payload() if batch is not None else None,
                "audit_json": audit.trace_payload() if audit is not None else "{}",
            }
        )

    def _record_scan_skip(
        self,
        now: datetime,
        *,
        trigger_type: str,
        detail: str,
        health: HealthState,
        task_key: str | None,
    ) -> int:
        return self.store.record_scan_run(
            {
                "started_at": now.isoformat(),
                "completed_at": now.isoformat(),
                "trigger_type": trigger_type,
                "task_key": task_key,
                "health": health.value,
                "detail": detail,
                "audit_json": json.dumps(
                    {"skip_reason": detail},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        )

    def _execute_summary_task(
        self,
        now: datetime,
        spec: AutomationTaskSpec,
        *,
        catch_up: bool = False,
    ) -> None:
        if self._summary_retry_at is not None and now < self._summary_retry_at:
            return
        self._mark_task_running(spec, now)
        if self._generate_summary(now, catch_up=catch_up):
            self._mark_task_succeeded(
                spec,
                now,
                detail=(
                    "15:30补生成总结（catch_up=true）。"
                    if catch_up
                    else "15:30本地总结已生成；外部增强可降级。"
                ),
            )
            return
        self.store.update_automation_task(
            spec.task_key,
            state=AutomationTaskState.FAILED.value,
            updated_at=now.isoformat(),
            detail=self._summary_issue or "盘后总结生成失败。",
        )

    def _record_alert(
        self,
        now: datetime,
        trigger: AlertTrigger,
        decision: str,
        title: str,
        subtitle: str,
        *,
        detail: dict[str, object] | None = None,
    ) -> int:
        assert self.batch is not None
        snapshot_id = self.store.record_batch(self.batch)
        alert_id = self.store.record_alert_event(
            snapshot_id,
            now.isoformat(),
            decision,
            self._alert_client_platform,
            trigger_type=trigger.value,
            detail=detail,
        )
        self.pending_alert = PendingUiAlert(
            title=title,
            subtitle=subtitle,
            trigger_type=trigger.value,
        )
        if self.state is HealthState.HEALTHY and trigger in {
            AlertTrigger.SCHEDULED_0945,
            AlertTrigger.SCHEDULED_1445,
        }:
            self._record_scheduled_outcomes_safely(
                snapshot_id=snapshot_id,
                alert_id=alert_id,
                trigger=trigger,
                now=now,
            )
        return snapshot_id

    def _record_scheduled_outcomes_safely(
        self,
        *,
        snapshot_id: int,
        alert_id: int,
        trigger: AlertTrigger,
        now: datetime,
    ) -> None:
        tracker = self._outcome_tracker
        batch = self.batch
        if tracker is None or batch is None:
            return
        try:
            tracker.record_scheduled_batch(
                batch,
                snapshot_id=snapshot_id,
                alert_id=alert_id,
                trigger_type=trigger.value,
                recorded_at=now,
                resolve_calendar=False,
            )
        except Exception as error:  # noqa: BLE001 - alert is already durable
            self._outcome_issue = f"outcome-create:{type(error).__name__}"
            return
        self._submit_outcome_task(
            lambda: tracker.resolve_pending_targets(
                now=now,
                limit=3,
                entry_snapshot_id=snapshot_id,
            )
        )

    def _settle_candidate_outcomes_safely(
        self,
        trigger: AlertTrigger,
        outcome: ScanOutcome,
        now: datetime,
    ) -> None:
        tracker = self._outcome_tracker
        if tracker is None:
            return
        slot = (
            OutcomeSlot.MORNING if trigger is AlertTrigger.SCHEDULED_0945 else OutcomeSlot.AFTERNOON
        )

        def settle_old_outcomes() -> OutcomeActionReport:
            tracker.resolve_pending_targets(now=now, limit=3)
            return tracker.settle_fixed_slot(
                target_trade_date=now.date(),
                slot=slot,
                scan_quotes=outcome.quotes,
                now=now,
            )

        self._submit_outcome_task(settle_old_outcomes)
        self._submit_due_outcome_fallbacks(now)

    def _submit_due_outcome_fallbacks(self, now: datetime) -> None:
        tracker = self._outcome_tracker
        if tracker is None:
            return
        current = _shanghai(now)
        try:
            groups = tracker.due_backfill_groups(now=current, limit=4)
        except Exception as error:  # noqa: BLE001 - sidecar discovery stays isolated
            self._outcome_issue = f"outcome-discovery:{type(error).__name__}"
            return
        for target_date, target_slot in groups:
            task_key = f"{target_date.isoformat()}:outcome-{target_slot.value}"
            with self._outcome_future_lock:
                already_submitted = task_key in self._outcome_fallback_submitted
            if already_submitted:
                continue

            def backfill_due(
                selected_tracker: CandidateOutcomeTracker = tracker,
                selected_date: date = target_date,
                selected_slot: OutcomeSlot = target_slot,
            ) -> OutcomeActionReport:
                return selected_tracker.backfill_due(
                    now=_shanghai(self._clock()),
                    target_trade_date=selected_date,
                    target_slot=selected_slot,
                    limit=3,
                )

            self._submit_outcome_task(backfill_due, fallback_key=task_key)

    def _schedule_initial_outcome_backfill(self, now: datetime) -> None:
        tracker = self._outcome_tracker
        if tracker is None or self._outcome_initial_backfill_submitted:
            return
        self._outcome_initial_backfill_submitted = True
        self._submit_outcome_task(
            lambda: tracker.backfill_recent_scheduled(
                now=now,
                days=30,
                settlement_limit=180,
            )
        )

    def _submit_outcome_task(
        self,
        task: Callable[[], OutcomeActionReport],
        *,
        fallback_key: str | None = None,
    ) -> bool:
        try:
            future = self._outcome_executor.submit(task)
        except RuntimeError as error:
            self._outcome_issue = f"outcome-submit:{type(error).__name__}"
            return False
        with self._outcome_future_lock:
            self._outcome_futures.add(future)
            if fallback_key is not None:
                self._outcome_fallback_submitted.add(fallback_key)

        def task_done(completed: Future[OutcomeActionReport]) -> None:
            self._outcome_task_done(completed, fallback_key=fallback_key)

        future.add_done_callback(task_done)
        return True

    def _outcome_task_done(
        self,
        future: Future[OutcomeActionReport],
        *,
        fallback_key: str | None = None,
    ) -> None:
        try:
            report = future.result()
            if fallback_key is not None and report.pending:
                self._outcome_issue = "outcome-retry-scheduled"
        except Exception as error:  # noqa: BLE001 - background sidecar stays isolated
            self._outcome_issue = f"outcome-background:{type(error).__name__}"
        finally:
            with self._outcome_future_lock:
                self._outcome_futures.discard(future)
                if fallback_key is not None:
                    self._outcome_fallback_submitted.discard(fallback_key)

    def _generate_summary(self, now: datetime, *, catch_up: bool = False) -> bool:
        """Generate the 15:30 summary with a local-first fallback.

        External daily/sector enrichment is useful but it must never erase the
        product obligation.  Any day with real scan or alert activity receives
        a deterministic local summary even when Pro endpoints, calendars or
        optional reports are unavailable.
        """
        trade_date = now.date().isoformat()
        if self._summary_date == trade_date:
            self._summary_date = trade_date
            return True
        existing_summary = self.store.get_daily_summary(trade_date)
        if existing_summary is not None:
            reports_dir = report_directory_for_database(self.store.path)
            full_json = reports_dir / f"{trade_date}-A股盘后回顾.json"
            local_json = reports_dir / f"{trade_date}-local-summary.json"
            local_pdf = reports_dir / f"{trade_date}-A股盘后回顾.pdf"
            if full_json.is_file():
                try:
                    record = json.loads(full_json.read_text(encoding="utf-8"))
                    if not isinstance(record, dict):
                        raise ValueError("完整盘后报告JSON格式无效")
                    source_version = str(record.get("version", "full-market-v1"))
                    source_generated_at = str(record.get("generated_at", ""))
                    if not manifest_is_current(
                        local_pdf,
                        source_path=full_json,
                        report_mode="full_market",
                        source_version=source_version,
                        source_generated_at=source_generated_at,
                    ):
                        temporary = local_pdf.with_name(f".{local_pdf.name}.tmp")
                        render_post_close_pdf(record, temporary)
                        temporary.replace(local_pdf)
                        write_pdf_manifest(
                            local_pdf,
                            source_path=full_json,
                            report_mode="full_market",
                            source_version=source_version,
                            source_generated_at=source_generated_at,
                        )
                    self._summary_date = trade_date
                    return True
                except Exception:
                    self._set_summary_retry(now)
                    return False
            if local_json.is_file():
                try:
                    source = json.loads(local_json.read_text(encoding="utf-8"))
                    if not isinstance(source, dict):
                        raise ValueError("本地总结JSON格式无效")
                    report = LocalFallbackReport.from_record(source)
                    if manifest_is_current(
                        local_pdf,
                        source_path=local_json,
                        report_mode="local_fallback",
                        source_version=report.source_version,
                        source_generated_at=report.source_generated_at,
                        source_commit_value=report.source_commit,
                    ):
                        self._summary_date = trade_date
                        return True
                except Exception:
                    # Rebuild from the durable SQLite summary below.  A stale or
                    # malformed artifact must never be treated as a successful
                    # 15:30 report merely because the file exists.
                    pass
            try:
                self._write_local_summary_report(existing_summary)
            except Exception:
                self._set_summary_retry(now)
                return False
            self._summary_date = trade_date
            return True
        if self._summary_retry_at is not None and now < self._summary_retry_at:
            return False
        history = [
            row
            for row in self.store.list_alert_history(now=now, days=1)
            if str(row.get("displayed_at", "")).startswith(trade_date)
        ]
        interruption_count = self.store.count_health_interruptions(trade_date)

        # Prefer the richer static post-close report when it is available.
        collection = None
        if self._provider is not None:
            try:
                collection = collect_post_close_review(
                    self._provider,
                    trade_date=now.date(),
                    generated_at=now,
                )
            except Exception:
                fallback = (
                    self._post_close_fallback_provider or self._build_super_post_close_provider()
                )
                if fallback is not None:
                    try:
                        collection = collect_post_close_review(
                            fallback,
                            trade_date=now.date(),
                            generated_at=now,
                        )
                    except Exception:
                        collection = None
                    else:
                        collection = replace(
                            collection,
                            optional_failures=tuple(
                                dict.fromkeys(
                                    (
                                        *collection.optional_failures,
                                        "primary_ordinary_pro_unavailable",
                                        "super_static_advanced_diagnostic_fallback",
                                    )
                                )
                            ),
                            retrospective_only=True,
                        )
        if collection is not None:
            try:
                summary = application_summary_record(
                    collection,
                    alert_count=len(history),
                    health_interruption_count=interruption_count,
                    continuity_evidence=self._collect_continuity_evidence(trade_date),
                )
                summary["catch_up"] = 1 if catch_up else 0
                self.store.record_daily_summary(summary)
                write_post_close_report(
                    collection,
                    reports_dir=report_directory_for_database(self.store.path),
                    alert_count=len(history),
                    health_interruption_count=interruption_count,
                    alert_timeline=alert_timeline_records(history),
                )
            except Exception:
                collection = None

        if collection is None:
            observations = self._local_summary_observations(trade_date)
            if not history and not observations:
                self._set_summary_retry(now)
                return False
            summary = (
                DailySummaryEngine()
                .generate(
                    trade_date=now.date(),
                    generated_at=now,
                    alert_history=history,
                    observation_history=observations,
                    health_interruption_count=interruption_count,
                    continuity_evidence=self._collect_continuity_evidence(trade_date),
                    catch_up=catch_up,
                    version="daily-summary-local-fallback-v1",
                )
                .as_record()
            )
            try:
                self.store.record_daily_summary(summary)
                self._write_local_summary_report(summary)
            except Exception:
                self._set_summary_retry(now)
                return False

        self.store.prune_daily_summaries(before=now.date() - timedelta(days=30))
        self._summary_date = trade_date
        self._summary_retry_at = None
        self._summary_issue = None
        return True

    def _collect_continuity_evidence(self, trade_date: str) -> str:
        """Report real continuity facts; never let lunch hide a trading gap."""
        runs = [
            row
            for row in self.store.list_scan_runs(trade_date)
            if row.get("completed_at") and row.get("health") == HealthState.HEALTHY.value
        ]
        timestamps = [
            parsed
            for row in runs
            if (parsed := _parsed_datetime(str(row["completed_at"]))) is not None
        ]
        sessions = self.store.list_runtime_sessions(trade_date)
        runtime_events: list[dict[str, object]] = []
        event_counts: dict[str, int] = {}
        for session in sessions:
            for event in self.store.list_runtime_events(str(session["session_id"])):
                occurred = str(event.get("occurred_at", ""))
                if not occurred.startswith(trade_date):
                    continue
                runtime_events.append(event)
                event_type = str(event.get("event_type", ""))
                event_counts[event_type] = event_counts.get(event_type, 0) + 1

        gaps = analyze_scan_gaps(
            timestamps,
            runtime_sessions=sessions,
            runtime_events=runtime_events,
        )
        parts = list(continuity_gap_summary_parts(gaps))
        if len(sessions) > 1:
            parts.append(f"进程重启{len(sessions) - 1}次")
        if event_counts.get("sleep_detected"):
            parts.append(f"睡眠{event_counts['sleep_detected']}次")
        if event_counts.get("scan_stalled"):
            parts.append(f"扫描器自恢复{event_counts['scan_stalled']}次")
        for task in self.store.list_automation_tasks(trade_date):
            key = str(task.get("task_key", ""))
            state = str(task.get("state", ""))
            if "09:45" in key:
                parts.append(f"09:45提醒{state}")
            elif "14:45" in key:
                parts.append(f"14:45提醒{state}")
            elif "15:30" in key:
                parts.append(f"15:30总结{state}")
        intraday_count = len(
            [
                row
                for row in self.store.list_alert_history(
                    now=datetime.combine(
                        datetime.fromisoformat(trade_date).date(),
                        time(15, 31),
                        tzinfo=SHANGHAI,
                    ),
                    days=1,
                )
                if str(row.get("trigger_type", "")).startswith("intraday")
            ]
        )
        if intraday_count:
            parts.append(f"盘中强异动提醒{intraday_count}批")
        universe = getattr(self._runtime, "universe", None)
        concept_state = (
            "已加载"
            if universe is not None and universe.concept_loaded
            else (
                self._universe_refresh_issue
                if self._universe_refresh_issue is not None
                else "未加载（使用行业上下文）"
            )
        )
        parts.append(f"概念缓存：{concept_state}")
        return "；".join(parts) if parts else "未记录到扫描或连续性事件。"

    def _local_summary_observations(self, trade_date: str) -> list[dict[str, object]]:
        rows = [
            row
            for row in self.store.list_scan_runs(trade_date)
            if row.get("health") == HealthState.HEALTHY.value and row.get("stable_batch_json")
        ]
        # Keep the report bounded while still representing the day.  Scan runs
        # remain fully available in SQLite for detailed audit.
        return [{"payload_json": str(row["stable_batch_json"])} for row in rows[-30:]]

    def _write_local_summary_report(self, summary: dict[str, object]) -> None:
        generated_at = _parsed_datetime(str(summary.get("generated_at", ""))) or self._clock()
        write_local_fallback_artifacts(
            self.store,
            summary,
            reports_dir=report_directory_for_database(self.store.path),
            now=generated_at,
            source_commit_value=source_commit(),
        )

    def _prune_history_if_due(self, now: datetime) -> None:
        """Run the bounded 30-day history cleanup at most once per day."""
        if self._history_pruned_date == now.date():
            return
        try:
            self.store.prune_history(before=now - timedelta(days=30))
        except Exception:
            # Historical cleanup is maintenance; it must never stop a healthy
            # realtime scan.  Surface a short actionable status for the UI and
            # retry on the next session tick.
            self._history_prune_issue = "历史清理暂未完成，将在下次检查重试。"
            self.status_issues = tuple(
                dict.fromkeys((*self.status_issues, self._history_prune_issue))
            )
            return
        self._history_pruned_date = now.date()
        self._history_prune_issue = None

    def _set_summary_retry(self, now: datetime) -> None:
        self._summary_retry_at = now + timedelta(seconds=60)
        self._summary_issue = "盘后回顾暂未生成，将在60秒后自动重试。"
        self.status_issues = tuple(dict.fromkeys((*self.status_issues, self._summary_issue)))

    def _build_super_post_close_provider(self) -> PostCloseDataProvider | None:
        try:
            if not self.credential_store.get(SUPER_CREDENTIAL):
                return None
        except Exception:
            return None
        transport = SuperTransport(
            self.settings.super_profile,
            lambda: self.credential_store.get(SUPER_CREDENTIAL),
            request_budget=self._request_budget,
        )
        return cast(
            PostCloseDataProvider,
            TushareProvider(
                CapabilityRouter(
                    transport,
                    transport,
                    mode=DataSourceMode.SUPER,
                )
            ),
        )

    def _is_open_date(self, now: datetime) -> bool:
        trade_date = now.date().isoformat()
        # Real activity is stronger evidence than a stale calendar cache.  The
        # 2026-08-03 acceptance had healthy scans but the cached open_dates
        # ended earlier, which silently suppressed the 15:30 obligation.
        if self.store.has_scan_activity(trade_date) or self._today_alerts(now):
            return True
        if self._runtime is not None and self._runtime.universe is not None:
            if now.date() in self._runtime.universe.open_dates:
                return True
        if self._provider is None:
            return now.date().weekday() < 5
        try:
            compact = now.date().strftime("%Y%m%d")
            result = self._provider.trading_dates(
                exchange="SSE",
                start_date=compact,
                end_date=compact,
                is_open="1",
            )
        except Exception:
            # A separately throttled calendar must not hide the 15:30 report.
            # Weekdays are admitted provisionally; the required full-market
            # daily response still fails closed on exchange holidays.
            return now.date().weekday() < 5
        if not result.records:
            # Some SDK versions collapse an HTTP 429 into an empty result.
            # The required target-date daily response remains the final gate.
            return now.date().weekday() < 5
        return any(
            str(record.get("cal_date", "")).replace("-", "") == compact
            and str(record.get("is_open", "")).casefold() in {"1", "true", "y", "yes"}
            for record in result.records
        )

    def _refresh_credential_state(self) -> None:
        if isinstance(self.credential_store, KeyringCredentialStore):
            cached, secret = self.credential_store.get_cached(PRIMARY_CREDENTIAL)
            if not cached:
                self._credential_state = "unknown"
                self._credential_error = None
                self._primary_secret_snapshot = None
                self._set_credential_pending()
                return
            self._primary_secret_snapshot = secret
            self._credential_state = "present" if secret else "missing"
        elif self._primary_present():
            self._credential_state = "present"
        else:
            self._credential_state = "missing"
        self._credential_error = None
        if self._credential_state == "present":
            self.connection_state = TqConnectionState.CHECKING
            self.connection_detail = "Token已配置，等待实时检测。"
            self.status_issues = ()
        else:
            self._set_missing_credential()

    def _primary_secret(self) -> str | None:
        if isinstance(self.credential_store, KeyringCredentialStore):
            return self._primary_secret_snapshot
        try:
            return self.credential_store.get(PRIMARY_CREDENTIAL)
        except Exception:
            return None

    def _primary_present(self) -> bool:
        return bool(self._primary_secret())

    def _set_credential_pending(self) -> None:
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.connection_detail = "正在读取系统钥匙串；读取完成前不会发起实时请求。"
        self.data_gate_label = "正在读取凭据"
        self.candidate_gate_label = "等待凭据检测"
        self.health_detail = self.connection_detail
        self.status_issues = ("Keychain 检查在后台执行，窗口保持可用。",)

    def _set_credential_error(self) -> None:
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.DISCONNECTED
        self.connection_detail = "系统钥匙串暂时不可用；未发起实时请求。"
        self.data_gate_label = "钥匙串不可用"
        self.candidate_gate_label = "等待钥匙串恢复"
        self.health_detail = self.connection_detail
        self.status_issues = ("请解锁 macOS 钥匙串或处理系统安全存储提示后重试。",)

    def _publish_credential_state(
        self,
        generation: int,
        state: str,
        *,
        primary_present: bool,
        legacy_present: bool,
        error_name: str | None,
    ) -> None:
        with self._credential_refresh_lock:
            if generation != self._credential_refresh_generation:
                return
            self._credential_refresh_in_flight = False
            callback = self._credential_callback
            self._credential_callback = None
        self._credential_state = state
        self._credential_error = error_name
        self._legacy_credential_present = legacy_present
        if primary_present:
            cached, secret = cast(KeyringCredentialStore, self.credential_store).get_cached(
                PRIMARY_CREDENTIAL
            )
            self._primary_secret_snapshot = secret if cached else None
        else:
            self._primary_secret_snapshot = None
        if state == "present":
            self.connection_state = TqConnectionState.CHECKING
            self.connection_detail = "Token已配置，等待实时检测。"
            self.data_gate_label = "等待检测"
            self.candidate_gate_label = "等待实时扫描"
            self.health_detail = "凭据读取完成，正在等待实时检测。"
            self.status_issues = ()
        elif state == "missing":
            self._set_missing_credential()
        else:
            self._set_credential_error()
        if callback is not None:
            callback()

    def _apply_pending_platform_recovery(self) -> bool:
        with self._platform_recovery_lock:
            reason = self._platform_recovery_reason
            self._platform_recovery_reason = None
        if reason is None:
            return False
        reset = getattr(self._runtime, "reset_for_external_recovery", None)
        if callable(reset):
            reset()
        self._recovery_round = 0
        self.record_platform_event(
            "warming_1_of_3",
            detail={"reason": reason},
        )
        self.pending_alert = None
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.data_gate_label = "重新预热"
        self.candidate_gate_label = "暂停新候选"
        self.connection_detail = reason
        self.health_detail = reason
        self.status_issues = ("旧实时基线已清理；需连续3轮新鲜完整数据后恢复。",)
        return True

    def _is_network_interrupted(self) -> bool:
        with self._platform_recovery_lock:
            return self._network_interrupted

    def _cancel_in_flight_scan(self) -> None:
        """Best-effort cancellation; fake runtimes used in tests may not opt in."""
        runtime = self._runtime
        cancel = getattr(runtime, "request_scan_cancellation", None)
        if callable(cancel):
            cancel()

    def _detect_scan_stall(self, now: datetime) -> None:
        """Rebuild the scan loop when no scan succeeded for > 90s in a session.

        Only applies during trading hours without a pending sleep/wake or
        network recovery; a sleep gap is explained by sleep events instead.
        """
        universe = getattr(self._runtime, "universe", None)
        open_dates = (
            universe.open_dates if universe is not None and universe.open_dates is not None else ()
        )
        if not _session_is_trading(self._schedule, now, open_dates):
            return
        with self._platform_recovery_lock:
            if self._platform_recovery_reason is not None or self._network_interrupted:
                return
        if self.last_scan_succeeded_at is None:
            return
        # The lunch break (11:30-13:00) and the pre-open gap are legitimate
        # pauses; only a stall inside the same trading block is actionable.
        if _trading_block(now) != _trading_block(self.last_scan_succeeded_at):
            return
        gap_seconds = (now - self.last_scan_succeeded_at).total_seconds()
        if gap_seconds <= self._stall_threshold_seconds:
            return
        detail = {
            "stall_started_at": self.last_scan_succeeded_at.isoformat(),
            "last_success_at": self.last_scan_succeeded_at.isoformat(),
            "gap_seconds": round(gap_seconds, 1),
            "active_request": self._active_scan_attempt_id is not None,
        }
        self.record_platform_event("scan_stalled", now=now, detail=detail)
        self._cancel_in_flight_scan()
        if self._active_scan_attempt_id is not None:
            self._finish_scan_attempt(
                self._active_scan_attempt_id,
                completed_at=now,
                state="cancelled",
                detail=f"scan-stalled gap={round(gap_seconds, 1)}s",
            )
        self.begin_platform_recovery(
            "检测到扫描停滞（超过90秒无成功扫描），已自动取消旧任务并重建扫描循环。"
        )

    def _capabilities_ready(self) -> bool:
        if self.capability_checks is None:
            return True
        self.capability_checks.start_background()
        return _required_capabilities_ready(self.capability_checks.statuses())

    def _start_universe_refresh(self, now: datetime) -> bool:
        if self._runtime is None:
            return False
        if self._universe_future is not None:
            return False
        if self._universe_retry_at is not None and now < self._universe_retry_at:
            return False
        runtime = self._runtime
        prepare = getattr(runtime, "prepare", None)
        if not callable(prepare):
            return False
        self._universe_future_runtime = runtime
        self._universe_future = self._universe_executor.submit(prepare)
        self._universe_refresh_issue = None
        return True

    def _poll_universe_refresh(self, now: datetime) -> None:
        future = self._universe_future
        runtime = self._universe_future_runtime
        if future is None or not future.done():
            return
        self._universe_future = None
        self._universe_future_runtime = None
        try:
            prepared = future.result()
        except ProviderError as error:
            retry_seconds = (
                error.retry_after_seconds
                if error.reason is ProviderFailureReason.RATE_LIMITED
                else 60.0
            )
            self._universe_retry_at = now + timedelta(
                seconds=retry_seconds if retry_seconds is not None else 60.0
            )
            self._universe_refresh_issue = (
                "基础数据暂时限流；实时路线未调用普通Pro，等待后台缓存恢复。"
                if error.reason is ProviderFailureReason.RATE_LIMITED
                else "基础缓存刷新失败，将在60秒后后台重试。"
            )
            return
        except Exception as error:
            self._universe_retry_at = now + timedelta(seconds=60)
            self._universe_refresh_issue = _safe_universe_refresh_issue(error)
            return
        if runtime is not self._runtime:
            return
        self._prepared_date = now.date() if now.date() in prepared.open_dates else None
        reason = getattr(getattr(runtime, "loader", None), "last_concept_failure", None)
        preserved = bool(getattr(runtime, "concept_cache_preserved", False))
        if prepared.concept_loaded and not preserved:
            self._universe_retry_at = None
            self._universe_refresh_issue = None
        elif prepared.concept_loaded and preserved:
            self._universe_retry_at = now + timedelta(minutes=5)
            self._universe_refresh_issue = (
                "概念刷新失败，当前进程继续使用上次成功概念缓存；"
                "行业筛选与概念筛选均保持可用，5分钟后重试" + (f"（{reason}）" if reason else "。")
            )
        else:
            self._universe_retry_at = now + timedelta(minutes=5)
            self._universe_refresh_issue = (
                "概念刷新失败，已保留上次成功概念缓存；行业筛选继续运行，5分钟后重试"
                + (f"（{reason}）" if reason else "。")
                if preserved
                else "概念板块暂未加载；行业筛选继续运行，5分钟后后台重试"
                + (f"（{reason}）" if reason else "。")
            )

    def _set_universe_warming(self, now: datetime) -> None:
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.data_gate_label = "基础缓存准备中"
        self.candidate_gate_label = "保留上次结果" if self.batch else "暂停新候选"
        self.connection_detail = (
            "第1/3步：正在批量准备股票名单、行业和三日趋势。"
            if self._manual_started_monotonic is not None
            else "Token已保存；实时扫描等待完整基础缓存。"
        )
        if self._universe_refresh_issue is not None:
            limited = "限流" in self._universe_refresh_issue
            self.data_gate_label = "基础数据限流" if limited else "基础准备失败"
            self.connection_detail = self._universe_refresh_issue
            self.health_detail = self._universe_refresh_issue
            self.last_fetch_detail = (
                "基础数据接口限流，本次未生成新Top3。"
                if limited
                else "基础数据准备失败，本次未生成新Top3。"
            )
            self.status_issues = (self._universe_refresh_issue,)
            return
        if self._universe_retry_at is not None and now < self._universe_retry_at:
            self.health_detail = "基础缓存尚未恢复，本轮未发起实时请求。"
            self.status_issues = (
                f"预计 {self._universe_retry_at.strftime('%H:%M:%S')} 后台重试。",
            )
            return
        self.health_detail = self.connection_detail
        if self._manual_started_monotonic is not None:
            self.last_fetch_detail = (
                f"第1/3步：基础数据准备中；预计剩余不超过 {self._manual_remaining_seconds()} 秒。"
            )
            self.status_issues = ("股票行业与日线均按全市场批量获取，不逐只循环。",)
        else:
            self.status_issues = ('实时扫描只会读取缓存并调用 realtime_quote(src="sina")。',)

    def _set_capability_warming(self) -> None:
        assert self.capability_checks is not None
        statuses = self.capability_checks.statuses()
        rate_limited = [
            status
            for status in statuses.values()
            if status.state is ProviderCapabilityState.RATE_LIMITED
        ]
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.data_gate_label = "后台准备"
        self.candidate_gate_label = "等待数据准备"
        if rate_limited:
            earliest = min(
                (
                    status.next_retry_at
                    for status in rate_limited
                    if status.next_retry_at is not None
                ),
                default=None,
            )
            retry_note = (
                f"预计 {earliest.strftime('%H:%M:%S')} 自动恢复。"
                if earliest is not None
                else "等待自动恢复。"
            )
            self.connection_detail = "接口暂时限流，已保存Token，等待自动恢复。"
            self.health_detail = self.connection_detail
            self.status_issues = (retry_note,)
            return
        self.connection_detail = "Token已保存，正在分项准备基础数据、实时行情和板块历史。"
        self.health_detail = "核心能力检测完成后将开始预热；资金和历史分钟未确认不会阻塞候选。"
        self.status_issues = (
            f"实时验证完成后需连续 {self._manual_required_scan_cycles()} 轮新鲜完整数据。",
        )

    def _set_realtime_capability_warming(self) -> None:
        assert self.capability_checks is not None
        statuses = self.capability_checks.statuses()
        realtime_statuses = tuple(
            statuses[capability]
            for capability in (
                ProviderCapability.REALTIME_1,
                ProviderCapability.REALTIME_100,
                ProviderCapability.REALTIME_300,
                ProviderCapability.REALTIME_800,
            )
        )
        rate_limited = [
            status
            for status in realtime_statuses
            if status.state is ProviderCapabilityState.RATE_LIMITED
        ]
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.data_gate_label = "实时批次检测中"
        self.candidate_gate_label = "等待实时验证"
        if rate_limited:
            earliest = min(
                (
                    status.next_retry_at
                    for status in rate_limited
                    if status.next_retry_at is not None
                ),
                default=None,
            )
            retry_note = (
                f"预计 {earliest.strftime('%H:%M:%S')} 自动恢复。"
                if earliest is not None
                else "等待自动恢复。"
            )
            self.connection_detail = "实时接口暂时限流，已保存Token，等待自动恢复。"
            self.health_detail = self.connection_detail
            self.status_issues = (retry_note,)
            return
        self.connection_detail = "正在按1只、100只、300只、800只验证实时接口。"
        self.health_detail = '验证只调用 realtime_quote(src="sina")，不会请求普通Pro。'
        if self._manual_started_monotonic is not None:
            self.last_fetch_detail = (
                f"第2/3步：实时批次验证中；预计剩余不超过 {self._manual_remaining_seconds()} 秒。"
            )
        self.status_issues = ("800只批次通过后才开始全市场七批扫描。",)

    def _manual_scan_is_ready(self) -> bool:
        if self._runtime is None:
            return False
        return _runtime_universe_is_usable(
            self._runtime,
            datetime.now(SHANGHAI),
        )

    def _manual_should_wait(self) -> bool:
        if self._universe_future is not None:
            return True
        if self._runtime is not None and _runtime_universe_is_usable(
            self._runtime,
            datetime.now(SHANGHAI),
        ):
            return self.state is HealthState.WARMING
        return False

    def _set_manual_scan_progress(
        self,
        scan_round: int,
        *,
        deadline: float,
    ) -> None:
        total = self._manual_required_scan_cycles()
        shown_round = min(scan_round, total)
        remaining = max(1, round(deadline - monotonic_time()))
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.data_gate_label = "全市场扫描中"
        self.candidate_gate_label = f"新鲜数据 {shown_round}/{total} 轮"
        self.connection_detail = (
            f"第3/3步：正在执行第 {shown_round}/{total} 轮全市场实时扫描，每轮约7批。"
        )
        self.health_detail = self.connection_detail
        self.last_fetch_detail = (
            f"第3/3步：全市场实时扫描 {shown_round}/{total} 轮；预计剩余不超过 {remaining} 秒。"
        )
        self.status_issues = ('只调用 realtime_quote(src="sina")；完成后立即显示并弹出3只。',)

    def _manual_required_scan_cycles(self) -> int:
        runtime = self._runtime
        if runtime is not None:
            health = getattr(runtime, "health", None)
            required = getattr(health, "required_cycles", None)
            if isinstance(required, int):
                return max(1, required)
        return max(1, DataHealthConfig().initial_cycles)

    def _set_manual_timeout(self) -> None:
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.CHECKING
        self.data_gate_label = "本次超过60秒"
        self.candidate_gate_label = "保留上次结果" if self.batch else "尚无新结果"
        self.connection_detail = "本次获取未在60秒内完成，已停止本次等待。"
        self.health_detail = self.connection_detail
        self.last_fetch_detail = "本次超过60秒，未生成新Top3。"
        self.status_issues = ("基础缓存仍会在后台安全准备；可看到明确阶段后再重试。",)

    def _manual_remaining_seconds(self) -> int:
        started = self._manual_started_monotonic
        if started is None:
            return round(self.manual_fetch_timeout_seconds)
        elapsed = monotonic_time() - started
        return max(1, round(self.manual_fetch_timeout_seconds - elapsed))

    def manual_fetch_remaining_seconds(self) -> int | None:
        if self._manual_started_monotonic is None:
            return None
        return self._manual_remaining_seconds()

    def data_source_controller(self) -> object:
        """Create the settings controller on this session's shared budget."""
        from .data_source_settings import runtime_data_source_controller

        return runtime_data_source_controller(
            self.provider_changed,
            credential_store=self.credential_store,
            request_budget=self._request_budget,
            capability_checks=self.capability_checks,
        )

    def _start_runtime_session(self) -> None:
        """Persist process provenance; audit failures never block the UI."""
        try:
            now = datetime.now(SHANGHAI).isoformat()
            self.store.start_runtime_session(
                session_id=self._runtime_session_id,
                pid=os.getpid(),
                ppid=os.getppid(),
                app_path=_application_path(),
                source_commit=source_commit(),
                started_at=now,
            )
            self._runtime_session_active = True
        except Exception as error:
            self._runtime_audit_issue = f"runtime-session:{type(error).__name__}"

    def heartbeat(self, *, now: datetime | None = None) -> None:
        """Write a lightweight heartbeat every timer tick."""
        if not self._runtime_session_active:
            return
        try:
            heartbeat_at = _shanghai(now or self._clock()).isoformat()
            self.store.heartbeat_runtime_session(
                self._runtime_session_id,
                heartbeat_at,
                last_scan_at=self.last_fetch_at.isoformat()
                if self.last_fetch_at is not None
                else None,
            )
        except Exception as error:
            self._runtime_audit_issue = f"runtime-heartbeat:{type(error).__name__}"

    def record_window_activation(self, *, now: datetime | None = None) -> None:
        if not self._runtime_session_active:
            return
        try:
            self.store.heartbeat_runtime_session(
                self._runtime_session_id,
                _shanghai(now or self._clock()).isoformat(),
                last_window_activation_at=_shanghai(now or self._clock()).isoformat(),
            )
        except Exception as error:
            self._runtime_audit_issue = f"window-activation:{type(error).__name__}"

    def record_platform_event(
        self,
        event_type: str,
        *,
        now: datetime | None = None,
        detail: dict[str, object] | None = None,
    ) -> None:
        if not self._runtime_session_active:
            return
        try:
            self.store.record_runtime_event(
                session_id=self._runtime_session_id,
                occurred_at=_shanghai(now or self._clock()).isoformat(),
                event_type=event_type,
                detail=detail,
            )
        except Exception as error:
            self._runtime_audit_issue = f"runtime-event:{type(error).__name__}"

    def mark_sleep(self, *, now: datetime | None = None, reason: str = "") -> None:
        """Persist a sleep event and cancel any scan still in flight."""
        occurred = _shanghai(now or self._clock())
        self.record_platform_event(
            "sleep_detected",
            now=occurred,
            detail={"reason": reason or "system-suspend"},
        )
        if not self._runtime_session_active:
            return
        try:
            self.store.heartbeat_runtime_session(
                self._runtime_session_id,
                occurred.isoformat(),
                last_sleep_at=occurred.isoformat(),
            )
        except Exception as error:
            self._runtime_audit_issue = f"runtime-sleep:{type(error).__name__}"
        self._cancel_in_flight_scan()
        if self._active_scan_attempt_id is not None:
            self._finish_scan_attempt(
                self._active_scan_attempt_id,
                completed_at=occurred,
                state="sleep_interrupted",
                detail="进入睡眠，扫描被取消",
            )

    def mark_wake(self, *, now: datetime | None = None, reason: str = "") -> None:
        """Persist a wake event for the audit trail; recovery follows."""
        occurred = _shanghai(now or self._clock())
        self.record_platform_event(
            "wake_detected",
            now=occurred,
            detail={"reason": reason or "system-wake"},
        )
        if not self._runtime_session_active:
            return
        try:
            self.store.heartbeat_runtime_session(
                self._runtime_session_id,
                occurred.isoformat(),
                last_wake_at=occurred.isoformat(),
            )
        except Exception as error:
            self._runtime_audit_issue = f"runtime-wake:{type(error).__name__}"

    def _begin_scan_attempt(self, *, now: datetime, operation: str) -> str | None:
        if not self._runtime_session_active:
            return None
        attempt_id = uuid.uuid4().hex
        try:
            self.store.start_scan_attempt(
                attempt_id=attempt_id,
                session_id=self._runtime_session_id,
                started_at=now.isoformat(),
                operation=operation,
                thread_name=current_thread().name,
                timer_active=True,
            )
        except Exception as error:
            self._runtime_audit_issue = f"scan-attempt-start:{type(error).__name__}"
            return None
        self._active_scan_attempt_id = attempt_id
        return attempt_id

    def _finish_scan_attempt(
        self,
        attempt_id: str | None,
        *,
        completed_at: datetime,
        state: str,
        detail: str,
    ) -> None:
        if attempt_id is None:
            return
        try:
            self.store.finish_scan_attempt(
                attempt_id,
                completed_at.isoformat(),
                state=state,
                detail=detail,
            )
        except Exception as error:
            self._runtime_audit_issue = f"scan-attempt-finish:{type(error).__name__}"
        finally:
            if self._active_scan_attempt_id == attempt_id:
                self._active_scan_attempt_id = None

    def shutdown(self, *, exit_reason: str = "menu_quit") -> None:
        with self._credential_refresh_lock:
            self._credential_refresh_generation += 1
            self._credential_refresh_in_flight = False
            self._credential_state_result = None
            self._credential_callback = None
        if self._credential_poll_timer is not None:
            self._credential_poll_timer.stop()
        if self._runtime_session_active:
            try:
                self.store.end_runtime_session(
                    self._runtime_session_id,
                    _shanghai(self._clock()).isoformat(),
                    exit_reason=exit_reason,
                    graceful_exit=True,
                )
            except Exception as error:
                self._runtime_audit_issue = f"runtime-end:{type(error).__name__}"
            self._runtime_session_active = False
        if self.capability_checks is not None:
            self.capability_checks.shutdown()
        self._universe_executor.shutdown(wait=False, cancel_futures=True)
        with self._outcome_future_lock:
            for future in self._outcome_futures:
                future.cancel()
        self._outcome_executor.shutdown(wait=False, cancel_futures=True)

    def _set_missing_credential(self) -> None:
        legacy_present = self._legacy_credential_present
        if not isinstance(self.credential_store, KeyringCredentialStore):
            try:
                legacy_present = bool(self.credential_store.get(FAST_CREDENTIAL))
            except Exception:
                legacy_present = False
        self.state = HealthState.WARMING
        self.connection_state = TqConnectionState.DISCONNECTED
        self.connection_detail = "尚未设置统一 Tushare Token。"
        self.data_gate_label = "需要设置Token"
        self.candidate_gate_label = "等待设置"
        self.health_detail = "请打开“设置 → 数据接口”。"
        self.status_issues = (
            (
                "检测到本机旧 Token，可在数据接口设置中确认迁移。"
                if legacy_present
                else "请输入 Tushare Token 并测试保存。"
            ),
        )

    def _set_data_failure(
        self,
        detail: str,
        *,
        now: datetime | None = None,
    ) -> None:
        self.state = HealthState.STOPPED
        self._record_interruption(
            now or _shanghai(self._clock()),
            HealthState.STOPPED,
            "bootstrap-failure",
        )
        self.connection_state = TqConnectionState.DISCONNECTED
        self.connection_detail = detail
        self.data_gate_label = "数据中断"
        self.candidate_gate_label = "保留上次结果" if self.batch else "无新结果"
        self.health_detail = detail
        self.status_issues = (detail,)

    def _record_interruption(
        self,
        now: datetime,
        state: HealthState,
        reason: str,
    ) -> None:
        """Persist one bounded, credential-free record per outage onset."""
        if self._failure_active:
            return
        representative = (
            self.batch.candidates[0] if self.batch is not None and self.batch.candidates else None
        )
        provider_version = (
            representative.provider_version if representative is not None else "tushare-15000"
        )
        config_version = (
            representative.config_version
            if representative is not None
            else "v1-real-candidates-20260729"
        )
        self.store.record_health_metric(
            {
                "source_ts": now.isoformat(),
                "received_ts": now.isoformat(),
                "state": state.value,
                "provider_version": provider_version,
                "config_version": config_version,
                "detail": reason,
            }
        )
        self._failure_active = True


def _application_path() -> str:
    executable = Path(sys.executable).resolve()
    for parent in (executable, *executable.parents):
        if parent.suffix == ".app":
            return str(parent)
    return str(Path(sys.argv[0]).resolve())


def _runtime_factory(
    settings: DataSourceSettings,
    credential_store: CredentialStore,
    *,
    request_budget: ApplicationRequestBudget | None = None,
    universe_cache_path: Path | None = None,
) -> tuple[TushareV1Runtime, Tushare15000Provider]:
    def read_primary_secret() -> str | None:
        if isinstance(credential_store, KeyringCredentialStore):
            cached, secret = credential_store.get_cached(PRIMARY_CREDENTIAL)
            return secret if cached else None
        try:
            return credential_store.get(PRIMARY_CREDENTIAL)
        except Exception:
            return None

    budget = request_budget or ApplicationRequestBudget(settings.request_budget_interval_seconds)
    pro = TushareSdkProTransport(
        settings.primary_profile,
        read_primary_secret,
        request_budget=budget,
    )
    realtime = NativeRealtimeTransport(
        settings.native_realtime_profile,
        read_primary_secret,
        request_budget=budget,
    )
    provider = Tushare15000Provider(pro, realtime)
    coordinator = FullMarketScanCoordinator(
        realtime,
        minimum_coverage_ratio=0.99,
        max_source_span_seconds=settings.full_scan_max_seconds,
        max_quote_age_seconds=settings.source_fresh_seconds,
    )
    health = DataHealthTracker(
        DataHealthConfig(
            fresh_seconds=settings.source_fresh_seconds,
            stop_seconds=settings.source_stop_seconds,
            recovery_cycles=settings.realtime_warmup_cycles,
        )
    )
    return (
        TushareV1Runtime(
            TushareBootstrapLoader(provider),
            coordinator,
            health=health,
            universe_cache=(
                RuntimeUniverseCache(universe_cache_path)
                if universe_cache_path is not None
                else None
            ),
            universe_seed_path=packaged_universe_seed_path(),
        ),
        provider,
    )


def _is_preopen(now: datetime) -> bool:
    current = now.timetz().replace(tzinfo=None)
    return time(9, 15) <= current < time(9, 30)


def _supports_outcome_provider(provider: object) -> bool:
    return all(
        callable(getattr(provider, method, None))
        for method in ("trading_dates", "realtime_quotes", "historical_minutes")
    )


def _initial_outcome_backfill_window(now: datetime) -> bool:
    current = now.timetz().replace(tzinfo=None)
    return now.weekday() >= 5 or current < time(9, 0) or current >= time(15, 31)


def _phase(now: datetime) -> str:
    current = now.timetz().replace(tzinfo=None)
    return "上午盘中观察" if current < time(11, 31) else "下午盘中观察"


def _visible_phase(now: datetime) -> str:
    current = now.timetz().replace(tzinfo=None)
    if time(9, 30) <= current <= time(11, 30):
        return "上午盘中观察"
    if time(13, 0) <= current <= time(15, 0):
        return "下午盘中观察"
    if time(11, 30) < current < time(13, 0):
        return "午间休市"
    if time(9, 15) <= current < time(9, 30):
        return "开盘前准备"
    if current > time(15, 0):
        return "已收盘"
    return "非交易时段"


def _safe_universe_refresh_issue(error: Exception) -> str:
    safe_reasons = {
        "证券列表覆盖不足",
        "行业成分覆盖不足",
        "已完成日线不足4个交易日，保留上一版基础缓存",
        "incomplete",
        "stale",
        "corrupt",
        "io",
    }
    reason = str(error)
    if reason in safe_reasons:
        return f"基础数据准备失败：{reason}；将在60秒后后台重试。"
    return "基础缓存刷新失败，将在60秒后后台重试。"


def _shanghai(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)


def _parsed_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _shanghai(datetime.fromisoformat(value))
    except ValueError:
        return None


def _required_capabilities_ready(
    statuses: dict[ProviderCapability, ProviderCapabilityStatus],
) -> bool:
    required = (
        ProviderCapability.STOCK_LIST,
        ProviderCapability.TRADE_CALENDAR,
        ProviderCapability.SECTOR_CLASSIFICATION,
        ProviderCapability.REALTIME_1,
        ProviderCapability.REALTIME_100,
        ProviderCapability.REALTIME_300,
        ProviderCapability.REALTIME_800,
    )
    return bool(statuses) and all(
        statuses[capability].state is ProviderCapabilityState.AVAILABLE for capability in required
    )


def _realtime_capabilities_ready(
    statuses: dict[ProviderCapability, ProviderCapabilityStatus],
) -> bool:
    required = (
        ProviderCapability.REALTIME_1,
        ProviderCapability.REALTIME_100,
        ProviderCapability.REALTIME_300,
        ProviderCapability.REALTIME_800,
    )
    return bool(statuses) and all(
        statuses[capability].state is ProviderCapabilityState.AVAILABLE for capability in required
    )


def _runtime_universe_is_current(
    runtime: TushareV1Runtime,
    now: datetime,
) -> bool:
    checker = getattr(runtime, "universe_is_current", None)
    if callable(checker):
        return bool(checker(now))
    return runtime.universe is not None


def _runtime_universe_is_usable(
    runtime: TushareV1Runtime,
    now: datetime,
) -> bool:
    checker = getattr(runtime, "universe_is_usable", None)
    if callable(checker):
        return bool(checker(now))
    return runtime.universe is not None


def _session_is_trading(
    schedule: MarketSessionSchedule,
    now: datetime,
    open_dates: tuple[date, ...],
) -> bool:
    if schedule.is_trading(now, open_dates):
        return True
    # A stale calendar cache must not prevent the realtime route from proving
    # whether the market is open.  Weekdays are admitted provisionally; quote
    # freshness and coverage remain the fail-closed authority on holidays.
    if now.date().weekday() >= 5:
        return False
    current = now.timetz().replace(tzinfo=None)
    return time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0)


def _alert_trigger_for_task(spec: AutomationTaskSpec | None) -> AlertTrigger | None:
    if spec is None:
        return None
    if spec.task_type is AutomationTaskType.FIXED_0945:
        return AlertTrigger.SCHEDULED_0945
    if spec.task_type is AutomationTaskType.FIXED_1445:
        return AlertTrigger.SCHEDULED_1445
    return None


def _automation_spec_for_trigger(
    planner: AutomationPlanner,
    trigger: AlertTrigger | None,
    trade_date: date,
) -> AutomationTaskSpec | None:
    if trigger is None:
        return None
    expected = (
        AutomationTaskType.FIXED_0945
        if trigger is AlertTrigger.SCHEDULED_0945
        else AutomationTaskType.FIXED_1445
        if trigger is AlertTrigger.SCHEDULED_1445
        else None
    )
    if expected is None:
        return None
    return next(
        (spec for spec in planner.for_date(trade_date) if spec.task_type is expected),
        None,
    )


def _payload_codes(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return set()
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(candidates, list):
        return set()
    return {
        str(candidate.get("code"))
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("code")
    }


def _trading_block(ts: datetime) -> int:
    """Return 1 (morning), 2 (afternoon) or 0 (outside trading hours)."""
    current = ts.timetz().replace(tzinfo=None)
    if time(9, 30) <= current <= time(11, 30):
        return 1
    if time(13, 0) <= current <= time(15, 0):
        return 2
    return 0
