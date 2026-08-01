from __future__ import annotations

import json
import sys
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from threading import Lock
from time import monotonic as monotonic_time
from time import sleep as sleep_seconds
from typing import cast

from stock_watcher.config import DataSourceMode, DataSourceSettings
from stock_watcher.domain import SHANGHAI, HealthState
from stock_watcher.engine import (
    AlertPolicy,
    AlertTrigger,
    CandidateBatch,
    FundCapability,
)
from stock_watcher.paths import (
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
    DataHealthConfig,
    DataHealthTracker,
    FullMarketScanCoordinator,
    MarketSessionSchedule,
    RuntimeUniverse,
    RuntimeUniverseCache,
    ScanOutcome,
    TushareBootstrapLoader,
    TushareV1Runtime,
    alert_timeline_records,
    application_summary_record,
    collect_post_close_review,
    write_post_close_report,
)
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
        self.app_badge = "Mac V1" if sys.platform == "darwin" else "Windows V1"
        self._alert_client_platform = (
            "macos-desktop" if sys.platform == "darwin" else "windows-desktop"
        )
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
        """Open the simple Token page on first macOS launch without a Token."""
        return (
            self.settings.mode is DataSourceMode.TUSHARE_15000
            and not self._primary_present()
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
        if self._runtime is not None:
            self._runtime.request_scan_cancellation()
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
        if self._runtime is not None:
            self._runtime.request_scan_cancellation()
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
        self._prune_history_if_due(now)
        self.phase_label = _visible_phase(now)
        if self._is_network_interrupted():
            # Qt's reachability signal already placed the UI in STOPPED.  The
            # scheduled worker must not probe or scan again until a positive
            # recovery signal has cleared this external interruption.
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
            return None
        secret_present = self._primary_present()
        if not secret_present:
            self._set_missing_credential()
            return None
        self._apply_pending_platform_recovery()
        if self._runtime is None or self._provider is None:
            self._runtime, self._provider = self._runtime_factory(
                self.settings,
                self.credential_store,
            )
            if (
                self._runtime.universe is not None
                and now.date() in self._runtime.universe.open_dates
            ):
                self._prepared_date = now.date()
        assert self._runtime is not None
        if self._schedule.summary_due(now) and self._is_open_date(now):
            # At 15:30 today's completed daily bar makes the intraday universe
            # intentionally stale. Generate the close report before starting
            # that cache refresh, otherwise a Pro 429 can hide the report gate.
            self._generate_summary(now)
        self._poll_universe_refresh(now)
        if self.capability_checks is not None and self._runtime.universe is not None:
            self.capability_checks.seed_realtime_codes(
                security.code for security in self._runtime.universe.securities
            )
        if not _runtime_universe_is_current(self._runtime, now):
            self._start_universe_refresh(now)
            self._set_universe_warming(now)
            return None
        if (
            self.capability_checks is not None
            and self._capability_checks_required
            and not _realtime_capabilities_ready(self.capability_checks.statuses())
        ):
            self.capability_checks.start_realtime_background()
            self._set_realtime_capability_warming()
            return None
        open_dates = (
            self._runtime.universe.open_dates
            if self._runtime.universe is not None
            else ()
        )
        if not force and not self._schedule.is_trading(now, open_dates):
            if self.capability_checks is not None:
                self.capability_checks.start_background()
            self.state = HealthState.WARMING
            self.connection_state = TqConnectionState.CONNECTED
            self.connection_detail = "Token已配置。"
            self.data_gate_label = "非交易时段"
            self.candidate_gate_label = "上次结果" if self.batch else "等待开盘"
            self.phase_label = _visible_phase(now)
            self.health_detail = "非交易时段不发起全市场实时扫描。"
            self.status_issues = (
                (self._summary_issue,)
                if self._summary_issue is not None
                else ()
            )
            return None
        self.last_fetch_at = now
        outcome = self._runtime.scan_once()
        if self._is_network_interrupted():
            # An interruption landed while the request was in flight.  Its
            # cancellation outcome must not overwrite the fail-closed state.
            return None
        if self._apply_pending_platform_recovery():
            # A sleep/wake or network event arrived while the request was in
            # flight.  The cancelled response is intentionally not ranked,
            # persisted or allowed to trigger an old alert.
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
            "实时行情与板块数据正常。"
            if outcome.health is HealthState.HEALTHY
            else outcome.detail
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
            self.status_issues = (
                f"预计约 {max(1, round(remaining))} 秒后从失败环节继续检测。",
            )
        elif outcome.health is HealthState.HEALTHY:
            self.data_gate_label = "运行正常"
            self.candidate_gate_label = "3只观察"
            self.phase_label = _phase(now)
            fund_issue = "资金未确认，本轮只使用价格、板块和三日趋势。"
            if (
                self._runtime.universe is not None
                and self._runtime.universe.fund_capability.capability
                is FundCapability.DAILY_ONLY
            ):
                fund_issue = "资金接口仅有日级数据，不作为盘中增强依据。"
            self.status_issues = (
                (fund_issue,)
                if outcome.batch and outcome.batch.fund_module == "unavailable"
                else ()
            )
        elif outcome.health is HealthState.WARMING:
            required_cycles = self._manual_required_scan_cycles()
            self.data_gate_label = "正在准备"
            self.candidate_gate_label = "保留上次结果" if self.batch else "准备中"
            self.status_issues = (
                f"当前需连续 {required_cycles} 轮新鲜完整数据后恢复。",
            )
        else:
            self.data_gate_label = "数据中断"
            self.candidate_gate_label = "保留上次结果" if self.batch else "无新结果"
            self.status_issues = ("本轮未生成新候选。",)
        completed_at = _shanghai(self._clock())
        crossed = self._schedule.crossed_fixed_trigger(now, completed_at)
        self._evaluate_alerts(
            now if self._schedule.fixed_trigger(now) is not None else completed_at,
            outcome.strong_event,
            forced_fixed=crossed,
        )
        if (
            manual_request
            and outcome.health is HealthState.HEALTHY
            and self.batch is not None
            and len(self.batch.candidates) == 3
            and self.pending_alert is None
        ):
            self.store.record_batch(self.batch)
            self.pending_alert = PendingUiAlert(
                title="当前最新3只",
                subtitle=(
                    "本轮整体偏弱"
                    if self.batch.overall_weak
                    else "本次手动获取已完成"
                ),
                trigger_type="manual",
            )
        return outcome

    def _evaluate_alerts(
        self,
        now: datetime,
        strong_event: object | None,
        *,
        forced_fixed: AlertTrigger | None = None,
    ) -> None:
        if self.batch is None or len(self.batch.candidates) != 3:
            return
        fixed = forced_fixed or self._schedule.fixed_trigger(now)
        if fixed is not None:
            if any(
                row.get("trigger_type") == fixed.value
                for row in self._today_alerts(now)
            ):
                return
            decision = self._alert_policy.decide(self.batch, now, fixed)
            if decision.should_alert:
                title = (
                    "09:45 观察提醒"
                    if fixed is AlertTrigger.SCHEDULED_0945
                    else "14:45 观察提醒"
                )
                subtitle = (
                    "数据延迟，展示上次结果"
                    if self.state is not HealthState.HEALTHY
                    else ("本轮整体偏弱" if self.batch.overall_weak else "当前最新3只")
                )
                self._record_alert(now, fixed, decision.reason, title, subtitle)
            return
        from stock_watcher.engine import StrongMovementEvent

        if not isinstance(strong_event, StrongMovementEvent):
            return
        today_intraday = [
            row
            for row in self._today_alerts(now)
            if row.get("trigger_type") == AlertTrigger.INTRADAY.value
        ]
        if len(today_intraday) >= self._alert_policy.config.daily_limit:
            return
        cooldown_cutoff = now - self._alert_policy.config.cooldown
        for row in today_intraday:
            displayed = _parsed_datetime(row.get("displayed_at"))
            if displayed is None or displayed < cooldown_cutoff:
                continue
            if set(strong_event.triggering_codes) & _payload_codes(
                row.get("payload_json")
            ):
                return
        decision = self._alert_policy.decide(
            self.batch,
            now,
            AlertTrigger.INTRADAY,
            strong_movement=True,
            triggering_codes=strong_event.triggering_codes,
            event_strength=strong_event.strength,
        )
        if decision.should_alert:
            subtitle = (
                "个股与板块同步增强｜资金未确认"
                if strong_event.funds_unconfirmed
                else "个股、板块与资金同步增强"
            )
            self._record_alert(
                now,
                AlertTrigger.INTRADAY,
                decision.reason,
                "盘中强异动",
                subtitle,
            )

    def _today_alerts(self, now: datetime) -> list[dict[str, object]]:
        return [
            row
            for row in self.store.list_alert_history(now=now, days=1)
            if str(row.get("displayed_at", "")).startswith(now.date().isoformat())
        ]

    def _record_alert(
        self,
        now: datetime,
        trigger: AlertTrigger,
        decision: str,
        title: str,
        subtitle: str,
    ) -> None:
        assert self.batch is not None
        snapshot_id = self.store.record_batch(self.batch)
        self.store.record_alert_event(
            snapshot_id,
            now.isoformat(),
            decision,
            self._alert_client_platform,
            trigger_type=trigger.value,
        )
        self.pending_alert = PendingUiAlert(
            title=title,
            subtitle=subtitle,
            trigger_type=trigger.value,
        )

    def _generate_summary(self, now: datetime) -> None:
        trade_date = now.date().isoformat()
        if self._summary_date == trade_date:
            return
        if self._summary_retry_at is not None and now < self._summary_retry_at:
            return
        if self._provider is None:
            return
        history = [
            row
            for row in self.store.list_alert_history(now=now, days=1)
            if str(row.get("displayed_at", "")).startswith(trade_date)
        ]
        interruption_count = self.store.count_health_interruptions(trade_date)
        try:
            collection = collect_post_close_review(
                self._provider,
                trade_date=now.date(),
                generated_at=now,
            )
        except Exception:
            fallback = (
                self._post_close_fallback_provider
                or self._build_super_post_close_provider()
            )
            if fallback is None:
                self._set_summary_retry(now)
                return
            try:
                collection = collect_post_close_review(
                    fallback,
                    trade_date=now.date(),
                    generated_at=now,
                )
            except Exception:
                self._set_summary_retry(now)
                return
            collection = replace(
                collection,
                optional_failures=tuple(
                    dict.fromkeys(
                        (
                            *collection.optional_failures,
                            "primary_ordinary_pro_rate_limited",
                            "super_static_advanced_diagnostic_fallback",
                        )
                    )
                ),
                retrospective_only=True,
            )
        try:
            summary = application_summary_record(
                collection,
                alert_count=len(history),
                health_interruption_count=interruption_count,
            )
            self.store.record_daily_summary(summary)
            write_post_close_report(
                collection,
                reports_dir=report_directory_for_database(self.store.path),
                alert_count=len(history),
                health_interruption_count=interruption_count,
                alert_timeline=alert_timeline_records(history),
            )
            self.store.prune_daily_summaries(
                before=now.date() - timedelta(days=30)
            )
        except Exception:
            self._set_summary_retry(now)
            return
        self._summary_date = trade_date
        self._summary_retry_at = None
        self._summary_issue = None

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
        self.status_issues = tuple(
            dict.fromkeys((*self.status_issues, self._summary_issue))
        )

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
            )
        )

    def _is_open_date(self, now: datetime) -> bool:
        if self._runtime is not None and self._runtime.universe is not None:
            return now.date() in self._runtime.universe.open_dates
        if self._provider is None:
            return False
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
            and str(record.get("is_open", "")).casefold()
            in {"1", "true", "y", "yes"}
            for record in result.records
        )

    def _refresh_credential_state(self) -> None:
        if self._primary_present():
            self.connection_state = TqConnectionState.CHECKING
            self.connection_detail = "Token已配置，等待实时检测。"
            self.status_issues = ()
        else:
            self._set_missing_credential()

    def _primary_secret(self) -> str | None:
        try:
            return self.credential_store.get(PRIMARY_CREDENTIAL)
        except Exception:
            return None

    def _primary_present(self) -> bool:
        return bool(self._primary_secret())

    def _apply_pending_platform_recovery(self) -> bool:
        with self._platform_recovery_lock:
            reason = self._platform_recovery_reason
            self._platform_recovery_reason = None
        if reason is None:
            return False
        if self._runtime is not None:
            self._runtime.reset_for_external_recovery()
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
        self._universe_future_runtime = runtime
        self._universe_future = self._universe_executor.submit(runtime.prepare)
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
        self._universe_retry_at = None
        self._universe_refresh_issue = None

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
                "第1/3步：基础数据准备中；"
                f"预计剩余不超过 {self._manual_remaining_seconds()} 秒。"
            )
            self.status_issues = ("股票行业与日线均按全市场批量获取，不逐只循环。",)
        else:
            self.status_issues = (
                "实时扫描只会读取缓存并调用 realtime_quote(src=\"sina\")。",
            )

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
            f"实时验证完成后需连续 {self._manual_required_scan_cycles()} "
            "轮新鲜完整数据。",
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
        self.health_detail = "验证只调用 realtime_quote(src=\"sina\")，不会请求普通Pro。"
        if self._manual_started_monotonic is not None:
            self.last_fetch_detail = (
                "第2/3步：实时批次验证中；"
                f"预计剩余不超过 {self._manual_remaining_seconds()} 秒。"
            )
        self.status_issues = ("800只批次通过后才开始全市场七批扫描。",)

    def _manual_scan_is_ready(self) -> bool:
        if self._runtime is None:
            return False
        now = datetime.now(SHANGHAI)
        if not _runtime_universe_is_current(self._runtime, now):
            return False
        if self.capability_checks is None or not self._capability_checks_required:
            return True
        return _realtime_capabilities_ready(self.capability_checks.statuses())

    def _manual_should_wait(self) -> bool:
        if self._universe_future is not None:
            return True
        if self._runtime is not None and _runtime_universe_is_current(
            self._runtime,
            datetime.now(SHANGHAI),
        ):
            if self.capability_checks is None or not self._capability_checks_required:
                return self.state is HealthState.WARMING
            if _realtime_capabilities_ready(self.capability_checks.statuses()):
                return self.state is HealthState.WARMING
            if bool(getattr(self.capability_checks, "in_flight", False)):
                return True
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
            f"第3/3步：正在执行第 {shown_round}/{total} 轮全市场实时扫描，"
            "每轮约7批。"
        )
        self.health_detail = self.connection_detail
        self.last_fetch_detail = (
            f"第3/3步：全市场实时扫描 {shown_round}/{total} 轮；"
            f"预计剩余不超过 {remaining} 秒。"
        )
        self.status_issues = (
            "只调用 realtime_quote(src=\"sina\")；完成后立即显示并弹出3只。",
        )

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
        self.status_issues = (
            "基础缓存仍会在后台安全准备；可看到明确阶段后再重试。",
        )

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

    def shutdown(self) -> None:
        if self.capability_checks is not None:
            self.capability_checks.shutdown()
        self._universe_executor.shutdown(wait=False, cancel_futures=True)

    def _set_missing_credential(self) -> None:
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
            self.batch.candidates[0]
            if self.batch is not None and self.batch.candidates
            else None
        )
        provider_version = (
            representative.provider_version
            if representative is not None
            else "tushare-15000"
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


def _runtime_factory(
    settings: DataSourceSettings,
    credential_store: CredentialStore,
    *,
    request_budget: ApplicationRequestBudget | None = None,
    universe_cache_path: Path | None = None,
) -> tuple[TushareV1Runtime, Tushare15000Provider]:
    def secret_getter() -> str | None:
        return credential_store.get(PRIMARY_CREDENTIAL)

    budget = request_budget or ApplicationRequestBudget(
        settings.request_budget_interval_seconds
    )
    pro = TushareSdkProTransport(
        settings.primary_profile,
        secret_getter,
        request_budget=budget,
    )
    realtime = NativeRealtimeTransport(
        settings.native_realtime_profile,
        secret_getter,
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
        ),
        provider,
    )


def _is_preopen(now: datetime) -> bool:
    current = now.timetz().replace(tzinfo=None)
    return time(9, 15) <= current < time(9, 30)


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
        statuses[capability].state is ProviderCapabilityState.AVAILABLE
        for capability in required
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
        statuses[capability].state is ProviderCapabilityState.AVAILABLE
        for capability in required
    )


def _runtime_universe_is_current(
    runtime: TushareV1Runtime,
    now: datetime,
) -> bool:
    checker = getattr(runtime, "universe_is_current", None)
    if callable(checker):
        return bool(checker(now))
    return runtime.universe is not None


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
