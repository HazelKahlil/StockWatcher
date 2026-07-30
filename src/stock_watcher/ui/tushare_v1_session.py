from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

from stock_watcher.config import DataSourceMode, DataSourceSettings
from stock_watcher.domain import SHANGHAI, HealthState
from stock_watcher.engine import (
    AlertPolicy,
    AlertTrigger,
    CandidateBatch,
    DailySummaryEngine,
    FundCapability,
)
from stock_watcher.providers.tushare import ProProxyTransport, Tushare15000Provider
from stock_watcher.providers.tushare.capabilities import (
    CAPABILITY_ORDER,
    CapabilityCheckCoordinator,
    ProviderCapabilityState,
)
from stock_watcher.providers.tushare.native_realtime_transport import (
    NativeRealtimeTransport,
)
from stock_watcher.providers.tushare.rate_limit import ApplicationRequestBudget
from stock_watcher.runtime import (
    DataHealthConfig,
    DataHealthTracker,
    FullMarketScanCoordinator,
    MarketSessionSchedule,
    TushareBootstrapLoader,
    TushareV1Runtime,
)
from stock_watcher.security import (
    FAST_CREDENTIAL,
    PRIMARY_CREDENTIAL,
    CredentialStore,
    KeyringCredentialStore,
)
from stock_watcher.storage import SQLiteStore

from .tdx_session import TqConnectionState


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
    """Ordinary Windows session that continuously produces real stable Top3."""

    source_label = "A股全市场实时观察"
    phase_label = "非交易时段"
    app_badge = "Windows V1"
    window_title = "StockWatcher · 当前观察"
    is_replay = False
    supports_manual_fetch = False
    auto_check_interval_seconds = 10
    connection_name = "数据接口"
    reconnect_label = "重新检测"
    manual_fetch_label = "立即检测"
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
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = SQLiteStore(store_path)
        self.store.initialize()
        self.credential_store = credential_store or KeyringCredentialStore()
        self.settings = settings or DataSourceSettings()
        self._request_budget = ApplicationRequestBudget(
            self.settings.request_budget_interval_seconds
        )
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
                )

            self._runtime_factory: RuntimeFactory = budgeted_runtime_factory
        else:
            self._runtime_factory = runtime_factory
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self._schedule = MarketSessionSchedule()
        self._alert_policy = AlertPolicy()
        self._summary_engine = DailySummaryEngine()
        self._runtime: TushareV1Runtime | None = None
        self._provider: Tushare15000Provider | None = None
        self._prepared_date: date | None = None
        self._summary_date: str | None = None
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
        self._refresh_credential_state()

    def provider_changed(self, mode: DataSourceMode) -> None:
        self.settings = self.settings.model_copy(update={"mode": mode})
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

    def recover(self) -> None:
        self._run(force=False)

    def begin_manual_fetch(self) -> None:
        self.warm_and_recover()

    def manual_fetch(self) -> None:
        self._run(force=True)

    def consume_pending_alert(self) -> PendingUiAlert | None:
        pending = self.pending_alert
        self.pending_alert = None
        return pending

    def _run(self, *, force: bool) -> None:
        now = _shanghai(self._clock())
        self.last_connection_check = now
        if self.settings.mode is not DataSourceMode.TUSHARE_15000:
            self.state = HealthState.WARMING
            self.connection_state = TqConnectionState.NOT_APPLICABLE
            self.connection_detail = "已选择高级诊断；重启 StockWatcher 后打开。"
            self.data_gate_label = "等待重启"
            self.candidate_gate_label = "保留上次结果" if self.batch else "暂停"
            self.health_detail = self.connection_detail
            self.status_issues = (self.connection_detail,)
            return
        secret_present = self._primary_present()
        if not secret_present:
            self._set_missing_credential()
            return
        if self._capability_checks_required and not self._capabilities_ready():
            self._set_capability_warming()
            return
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
            self._generate_summary(now)
        open_dates = (
            self._runtime.universe.open_dates
            if self._runtime.universe is not None
            else ()
        )
        should_prepare_today = (
            self._prepared_date is not None
            and self._prepared_date != now.date()
            and (
                _is_preopen(now)
                or self._schedule.is_session_time(now)
            )
        )
        if should_prepare_today:
            try:
                prepared = self._runtime.prepare()
            except Exception:
                self._set_data_failure(
                    "今日数据准备失败，将按10秒周期重试。",
                    now=now,
                )
                return
            self._prepared_date = now.date()
            open_dates = prepared.open_dates
        if not force and not self._schedule.is_trading(now, open_dates):
            if self._runtime.universe is None and (
                _is_preopen(now) or self._schedule.is_session_time(now)
            ):
                try:
                    prepared = self._runtime.prepare()
                except Exception:
                    self._set_data_failure(
                        "开盘前数据准备失败，将按10秒周期重试。",
                        now=now,
                    )
                    return
                self._prepared_date = now.date()
                open_dates = prepared.open_dates
            if not self._schedule.is_trading(now, open_dates):
                self.state = HealthState.WARMING
                self.connection_state = TqConnectionState.CONNECTED
                self.connection_detail = "Token已配置。"
                self.data_gate_label = "非交易时段"
                self.candidate_gate_label = "上次结果" if self.batch else "等待开盘"
                self.phase_label = "非交易时段"
                self.health_detail = "非交易时段不发起全市场实时扫描。"
                self.status_issues = ()
                return
        self.last_fetch_at = now
        outcome = self._runtime.scan_once()
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
            remaining = self._request_budget.cooldown_remaining()
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
            self.data_gate_label = "正在准备"
            self.candidate_gate_label = "保留上次结果" if self.batch else "准备中"
            self.status_issues = ("恢复后需连续3轮新鲜完整数据。",)
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
            "windows-desktop",
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
        history = [
            row
            for row in self.store.list_alert_history(now=now, days=1)
            if str(row.get("displayed_at", "")).startswith(trade_date)
        ]
        closing_prices = self._closing_prices(now)
        summary = self._summary_engine.generate(
            trade_date=now.date(),
            generated_at=now,
            alert_history=history,
            closing_prices=closing_prices,
            health_interruption_count=self.store.count_health_interruptions(
                trade_date
            ),
        )
        self.store.record_daily_summary(summary.as_record())
        self._summary_date = trade_date

    def _closing_prices(self, now: datetime) -> dict[str, float]:
        if self._provider is None:
            return {}
        try:
            result = self._provider.daily_bars(
                trade_date=now.date().strftime("%Y%m%d")
            )
        except Exception:
            return {}
        prices: dict[str, float] = {}
        for record in result.records:
            code = record.get("ts_code")
            close = record.get("close")
            if isinstance(code, str) and isinstance(close, (str, int, float)):
                try:
                    prices[code] = float(close)
                except ValueError:
                    continue
        return prices

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
            return False
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

    def _capabilities_ready(self) -> bool:
        if self.capability_checks is None:
            return True
        self.capability_checks.start_background()
        statuses = self.capability_checks.statuses()
        return bool(statuses) and all(
            statuses[capability].state is ProviderCapabilityState.AVAILABLE
            for capability in CAPABILITY_ORDER
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
        self.health_detail = "能力检测完成后将开始预热；资金未确认不会阻塞候选。"
        self.status_issues = ("恢复后仍需连续3轮新鲜完整数据。",)

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
) -> tuple[TushareV1Runtime, Tushare15000Provider]:
    def secret_getter() -> str | None:
        return credential_store.get(PRIMARY_CREDENTIAL)

    budget = request_budget or ApplicationRequestBudget(
        settings.request_budget_interval_seconds
    )
    pro = ProProxyTransport(
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
        ),
        provider,
    )


def _is_preopen(now: datetime) -> bool:
    current = now.timetz().replace(tzinfo=None)
    return time(9, 15) <= current < time(9, 30)


def _phase(now: datetime) -> str:
    current = now.timetz().replace(tzinfo=None)
    return "上午盘中观察" if current < time(11, 31) else "下午盘中观察"


def _shanghai(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)


def _parsed_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _shanghai(datetime.fromisoformat(value))
    except ValueError:
        return None


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
