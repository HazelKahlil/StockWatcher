"""Headless StockWatcher business orchestrator.

This service is the single shared business owner for the Web Worker (and, in
the future, the Mac UI adapter). It reuses the exact engine/runtime/storage
modules the desktop session uses — no copied scoring, selection or policy
rules. It never imports the desktop UI package, PySide6, pyobjc or keyring.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from time import monotonic as monotonic_time
from time import sleep as sleep_seconds
from typing import Any

from stock_watcher.build_info import source_commit as default_source_commit
from stock_watcher.config import DataSourceMode, DataSourceSettings
from stock_watcher.domain import SHANGHAI, HealthState, OutcomeSlot, RealtimeQuote
from stock_watcher.engine import (
    AlertPolicy,
    AlertTrigger,
    CandidateBatch,
    DailySummaryEngine,
    StrongMovementEvent,
)
from stock_watcher.paths import (
    report_directory_for_database,
    universe_cache_path_for_database,
)
from stock_watcher.providers.tushare import Tushare15000Provider, TushareSdkProTransport
from stock_watcher.providers.tushare.errors import ProviderError
from stock_watcher.providers.tushare.native_realtime_transport import (
    NativeRealtimeTransport,
)
from stock_watcher.providers.tushare.rate_limit import ApplicationRequestBudget
from stock_watcher.providers.tushare.transport_protocol import TransportRequest
from stock_watcher.runtime import (
    AutomationPlanner,
    AutomationTaskSpec,
    AutomationTaskState,
    AutomationTaskType,
    CandidateOutcomeTracker,
    CandidateRepeatTracker,
    DataHealthConfig,
    DataHealthTracker,
    FullMarketScanCoordinator,
    MarketSessionSchedule,
    OutcomeActionReport,
    RepeatProjection,
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
from stock_watcher.runtime.post_close_pdf import render_post_close_pdf
from stock_watcher.runtime.post_close_report_model import LocalFallbackReport
from stock_watcher.runtime.repeat_tracker import REPEAT_BACKFILL_VERSION
from stock_watcher.storage import SQLiteStore

from .command_service import CommandService, CommandStatus, CommandType
from .event_outbox import EventOutbox
from .secret_service import SecretService

SHANGHAI_TZ = SHANGHAI
logger = logging.getLogger("stock_watcher.service")


def _shanghai(value: datetime) -> datetime:
    return value.replace(tzinfo=SHANGHAI) if value.tzinfo is None else value.astimezone(SHANGHAI)


def _parsed_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _payload_codes(value: object) -> set[str]:
    if not isinstance(value, str):
        return set()
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return set()
    if not isinstance(payload, dict):
        return set()
    codes: set[str] = set()
    for candidate in payload.get("candidates", []):
        if isinstance(candidate, dict) and isinstance(candidate.get("code"), str):
            codes.add(candidate["code"])
    return codes


def _trading_block(ts: datetime) -> int:
    current = ts.timetz().replace(tzinfo=None)
    if time(9, 30) <= current <= time(11, 30):
        return 1
    if time(13, 0) <= current <= time(15, 0):
        return 2
    return 0


def _format_duration(seconds: float) -> str:
    if seconds >= 60:
        return f"{int(seconds // 60)}分{int(seconds % 60)}秒"
    return f"{int(seconds)}秒"


RuntimeFactory = Callable[
    [DataSourceSettings, Callable[[], str | None]],
    tuple[TushareV1Runtime, Tushare15000Provider],
]


def default_runtime_factory(
    settings: DataSourceSettings,
    secret_getter: Callable[[], str | None],
    *,
    request_budget: ApplicationRequestBudget | None = None,
    universe_cache_path: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[TushareV1Runtime, Tushare15000Provider]:
    """Build the shared runtime exactly like the desktop factory (no Qt)."""
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
            clock=clock,
        ),
        provider,
    )


@dataclass(slots=True)
class ServiceConfig:
    settings: DataSourceSettings = field(default_factory=DataSourceSettings)
    source_commit: str = field(default_factory=default_source_commit)
    app_version: str = "0.6.0a4"
    report_dir: Path | None = None
    universe_cache_path: Path | None = None
    request_budget: ApplicationRequestBudget | None = None
    scan_interval_seconds: float = 10.0
    manual_timeout_seconds: float = 240.0
    universe_retry_seconds: float = 60.0
    stall_threshold_seconds: float = 90.0
    provider_version: str = "web-internal-test-v1"


@dataclass(frozen=True, slots=True)
class TickResult:
    scanned: bool = False
    snapshot_id: int | None = None
    alert_id: int | None = None
    events: tuple[dict[str, Any], ...] = ()
    command_ids: tuple[str, ...] = ()
    summary_done: bool = False
    skipped_reason: str | None = None


class StockWatcherService:
    """Continuous headless scan/alert/automation orchestrator.

    The Worker is the only process that instantiates this class; the Web layer
    reads projections and writes commands.
    """

    source_label = "A股全市场实时观察"
    manual_fetch_timeout_seconds = 60.0

    def __init__(
        self,
        store: SQLiteStore,
        *,
        config: ServiceConfig | None = None,
        runtime_factory: RuntimeFactory | None = None,
        outbox: EventOutbox | None = None,
        commands: CommandService | None = None,
        secrets: SecretService | None = None,
        clock: Callable[[], datetime] | None = None,
        source_commit: str | None = None,
        auto_start_session: bool = True,
    ) -> None:
        self.store = store
        self.config = config or ServiceConfig()
        if source_commit is not None:
            self.config = replace(self.config, source_commit=source_commit)
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self._outbox = outbox or EventOutbox(
            store,
            source_commit=self.config.source_commit,
            clock=self._clock,
        )
        self._commands = commands or CommandService(store, clock=self._clock)
        self._secrets = secrets
        self._runtime_factory = runtime_factory or (
            lambda settings, secret_getter: default_runtime_factory(
                settings,
                secret_getter,
                request_budget=self.config.request_budget,
                universe_cache_path=(
                    self.config.universe_cache_path
                    or universe_cache_path_for_database(store.path)
                ),
                clock=self._clock,
            )
        )
        self._schedule = MarketSessionSchedule()
        self._automation = AutomationPlanner()
        self._alert_policy = AlertPolicy()
        self._runtime: TushareV1Runtime | None = None
        self._provider: Tushare15000Provider | None = None
        self._prepared_date: date | None = None
        self._summary_date: str | None = None
        self._summary_retry_at: datetime | None = None
        self._summary_issue: str | None = None
        self._universe_retry_at: datetime | None = None
        self._history_pruned_date: date | None = None
        self._history_prune_issue: str | None = None
        self._failure_active = False
        self._recovery_round = 0
        self._runtime_session_id = uuid.uuid4().hex
        self._runtime_session_active = False
        self._active_scan_attempt_id: str | None = None
        self.batch: CandidateBatch | None = None
        self.state = HealthState.WARMING
        self.health_detail = "正在等待数据接口和交易时段。"
        self.last_scan_succeeded_at: datetime | None = None
        self.status_issues: tuple[str, ...] = ()
        self.last_fetch_at: datetime | None = None
        self.last_fetch_detail = "尚未完成实时扫描。"
        self._state_version = 0
        self._snapshot_id: int | None = None
        self._published_state: dict[str, Any] | None = None
        self._scan_lock = threading.Lock()
        self._outcome_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="stockwatcher-web-outcomes",
        )
        self._outcome_tracker: CandidateOutcomeTracker | None = None
        self._outcome_futures: set[Future[OutcomeActionReport]] = set()
        self._outcome_task_keys: set[str] = set()
        self._outcome_lock = threading.Lock()
        self._outcome_initial_backfill_done = False
        self._outcome_initial_backfill_retry_at: datetime | None = None
        self._outcome_unresolved_retry_at: datetime | None = None
        self._outcome_executor_closed = False
        self._repeat_tracker = CandidateRepeatTracker(store)
        self._repeat_backfill_done = False
        self._command_context = threading.local()
        self._holder_id = ""
        self._fencing_token = 0
        if auto_start_session:
            self._start_runtime_session()

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Start runtime evidence after the Worker has acquired its lease."""
        if not self._runtime_session_active:
            self._start_runtime_session()

    def cancel_inflight(self) -> None:
        """Invalidate any in-flight provider response before shutdown."""
        runtime = self._runtime
        if runtime is not None:
            runtime.request_scan_cancellation()

    def _start_runtime_session(self) -> None:
        try:
            now = datetime.now(SHANGHAI).isoformat()
            self.store.start_runtime_session(
                session_id=self._runtime_session_id,
                pid=0,
                ppid=0,
                app_path="stock-watcher-web-worker",
                source_commit=self.config.source_commit,
                started_at=now,
            )
            self._runtime_session_active = True
        except Exception:
            self._runtime_session_active = False

    def heartbeat(self, *, now: datetime | None = None) -> None:
        if not self._runtime_session_active:
            return
        stamp = _shanghai(now or self._clock()).isoformat()
        try:
            self.store.heartbeat_runtime_session(
                self._runtime_session_id,
                stamp,
                last_scan_at=(
                    self.last_scan_succeeded_at.isoformat()
                    if self.last_scan_succeeded_at is not None
                    else None
                ),
            )
        except KeyError:
            self._start_runtime_session()

    def stop(self, *, exit_reason: str = "worker_shutdown", graceful: bool = True) -> None:
        self.state = HealthState.STOPPED
        with self._outcome_lock:
            self._outcome_executor_closed = True
            futures = tuple(self._outcome_futures)
        for future in futures:
            future.cancel()
        # Do not close the store while a sidecar task can still be writing its
        # pending/settled rows.  Running provider calls are bounded by their
        # transport timeouts and the Web/Worker stop grace periods.
        self._outcome_executor.shutdown(wait=True, cancel_futures=True)
        if not self._runtime_session_active:
            return
        try:
            self.store.end_runtime_session(
                self._runtime_session_id,
                datetime.now(SHANGHAI).isoformat(),
                exit_reason=exit_reason,
                graceful_exit=graceful,
            )
        except KeyError:
            pass
        self._runtime_session_active = False

    # -- automation helpers ----------------------------------------------

    def _prepare_automation_tasks(self, now: datetime) -> tuple[AutomationTaskSpec, ...]:
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
            if saved is None or saved["state"] == AutomationTaskState.SUCCEEDED.value:
                continue
            due.append(spec)
        return tuple(due)

    def _expire_automation_tasks(self, now: datetime) -> None:
        for task in self.store.list_automation_tasks(now.date().isoformat()):
            if task["state"] in {
                AutomationTaskState.SUCCEEDED.value,
                AutomationTaskState.FAILED.value,
            }:
                continue
            deadline = _parsed_datetime(task.get("deadline_at"))
            if deadline is None or now <= deadline:
                continue
            self._mark_task(
                str(task["task_key"]),
                state=AutomationTaskState.FAILED,
                now=now,
                detail="超过产品截止时间仍未成功；保留失败证据。",
            )

    def _mark_task(
        self,
        task_key: str,
        *,
        state: AutomationTaskState,
        now: datetime,
        detail: str,
        snapshot_id: int | None = None,
        increment_attempt: bool = False,
    ) -> None:
        self.store.update_automation_task(
            task_key,
            state=state.value,
            updated_at=now.isoformat(),
            detail=detail,
            snapshot_id=snapshot_id,
            increment_attempt=increment_attempt,
        )
        saved = self.store.get_automation_task(task_key)
        if saved is not None:
            self._emit(
                event_type="automation.updated",
                payload={
                    "task_key": task_key,
                    "task_type": saved["task_type"],
                    "state": saved["state"],
                    "updated_at": saved["updated_at"],
                    "attempts": saved["attempts"],
                },
                source_kind="automation_task",
                source_id=task_key,
            )

    def _today_alerts(self, now: datetime) -> list[dict[str, object]]:
        return [
            row
            for row in self.store.list_alert_history(now=now, days=1)
            if str(row.get("displayed_at", "")).startswith(now.date().isoformat())
        ]

    # -- scan core -------------------------------------------------------

    def _ensure_runtime(self, now: datetime) -> bool:
        if self._runtime is None or self._provider is None:
            try:
                self._runtime, self._provider = self._runtime_factory(
                    self.config.settings,
                    self._secret_getter,
                )
            except Exception:
                self._runtime, self._provider = None, None
                return False
            if (
                self._runtime.universe is not None
                and now.date() in self._runtime.universe.open_dates
            ):
                self._prepared_date = now.date()
            self._ensure_outcome_tracker()
        return self._runtime is not None

    def _ensure_outcome_tracker(self) -> CandidateOutcomeTracker | None:
        provider = self._provider
        if self._outcome_tracker is not None:
            return self._outcome_tracker
        if provider is None or not all(
            callable(getattr(provider, method, None))
            for method in ("trading_dates", "realtime_quotes", "historical_minutes")
        ):
            return None
        self._outcome_tracker = CandidateOutcomeTracker(self.store, provider)
        return self._outcome_tracker

    def _submit_outcome_task(
        self,
        key: str,
        task: Callable[[], OutcomeActionReport],
    ) -> None:
        with self._outcome_lock:
            if self._outcome_executor_closed or key in self._outcome_task_keys:
                return
            self._outcome_task_keys.add(key)
            try:
                future = self._outcome_executor.submit(task)
            except RuntimeError:
                self._outcome_task_keys.discard(key)
                return
            self._outcome_futures.add(future)

        def completed(done: Future[OutcomeActionReport]) -> None:
            with self._outcome_lock:
                self._outcome_futures.discard(done)
                self._outcome_task_keys.discard(key)
            try:
                report = done.result()
            except Exception as error:  # noqa: BLE001 - sidecar remains isolated
                logger.warning("candidate outcome task failed: %s", type(error).__name__)
                if key == "history:30":
                    self._outcome_initial_backfill_done = False
                    self._outcome_initial_backfill_retry_at = _shanghai(
                        self._clock()
                    ) + timedelta(minutes=30)
                return
            if key == "history:30":
                self._outcome_initial_backfill_done = True
                self._outcome_initial_backfill_retry_at = None
            if report.pending and not self._outcome_executor_closed:
                # Read the persisted retry timestamps after each task. If one is
                # already due, enqueue it again; future retries remain owned by
                # SQLite and are rediscovered by later ticks or after restart.
                self._submit_due_outcome_backfills(_shanghai(self._clock()))
            if any((report.created, report.settled, report.pending, report.unavailable)):
                try:
                    self._outbox.append_own(
                        event_type="outcomes.updated",
                        payload={
                            "created": report.created,
                            "settled": report.settled,
                            "pending": report.pending,
                            "unavailable": report.unavailable,
                        },
                        source_kind="outcomes",
                        source_id=f"{key}:{uuid.uuid4().hex}",
                    )
                except Exception:
                    pass

        future.add_done_callback(completed)

    def _submit_initial_outcome_backfill(self, now: datetime) -> None:
        tracker = self._ensure_outcome_tracker()
        if tracker is None or self._outcome_initial_backfill_done:
            return
        if (
            self._outcome_initial_backfill_retry_at is not None
            and now < self._outcome_initial_backfill_retry_at
        ):
            return
        current = now.timetz().replace(tzinfo=None)
        if now.weekday() < 5 and time(9, 0) <= current < time(15, 31):
            return
        self._submit_outcome_task(
            "history:30",
            lambda: tracker.backfill_recent_scheduled(now=now, days=30),
        )

    def _submit_due_outcome_backfills(self, now: datetime) -> None:
        tracker = self._ensure_outcome_tracker()
        if tracker is None:
            return
        try:
            groups = tracker.due_backfill_groups(now=now)
        except Exception as error:  # noqa: BLE001 - outcome sidecar cannot fail the scan
            logger.warning("candidate outcome due discovery failed: %s", type(error).__name__)
            return
        for target_date, slot in groups:
            key = f"fallback:{target_date.isoformat()}:{slot.value}"

            def run_backfill(
                date_value: date = target_date,
                slot_value: OutcomeSlot = slot,
            ) -> OutcomeActionReport:
                return tracker.backfill_due(
                    now=_shanghai(self._clock()),
                    target_trade_date=date_value,
                    target_slot=slot_value,
                    limit=3,
                )

            self._submit_outcome_task(
                key,
                run_backfill,
            )

    def _submit_unresolved_outcome_targets(self, now: datetime) -> None:
        """Resolve calendar targets left pending by an interrupted sidecar."""
        tracker = self._ensure_outcome_tracker()
        if tracker is None:
            return
        if (
            self._outcome_unresolved_retry_at is not None
            and now < self._outcome_unresolved_retry_at
        ):
            return
        try:
            pending = self.store.list_pending_candidate_outcomes(
                unresolved_only=True,
                limit=1,
            )
        except Exception as error:  # noqa: BLE001 - outcome sidecar is isolated
            logger.warning(
                "candidate outcome unresolved discovery failed: %s",
                type(error).__name__,
            )
            return
        if not pending:
            self._outcome_unresolved_retry_at = None
            return

        def resolve_targets() -> OutcomeActionReport:
            report = tracker.resolve_pending_targets(
                now=_shanghai(self._clock()),
                limit=100,
            )
            self._outcome_unresolved_retry_at = (
                _shanghai(self._clock()) + timedelta(minutes=5)
                if report.safe_reasons
                else None
            )
            return report

        self._submit_outcome_task("resolve:unresolved-targets", resolve_targets)

    def _submit_fixed_outcome_settlement(
        self,
        now: datetime,
        trigger: AlertTrigger | None,
        scan_quotes: tuple[RealtimeQuote, ...],
    ) -> None:
        tracker = self._ensure_outcome_tracker()
        if trigger is None:
            return
        slot = {
            AlertTrigger.SCHEDULED_0945: OutcomeSlot.MORNING,
            AlertTrigger.SCHEDULED_1445: OutcomeSlot.AFTERNOON,
        }.get(trigger)
        if tracker is None or slot is None:
            return
        key = f"settle:{now.date().isoformat()}:{slot.value}"
        self._submit_outcome_task(
            key,
            lambda: tracker.settle_fixed_slot(
                target_trade_date=now.date(),
                slot=slot,
                scan_quotes=scan_quotes,
                now=now,
            ),
        )

    def _submit_scheduled_outcome_record(
        self,
        *,
        batch: CandidateBatch,
        snapshot_id: int,
        alert_id: int,
        trigger: AlertTrigger,
        recorded_at: datetime,
    ) -> None:
        tracker = self._ensure_outcome_tracker()
        if tracker is None or trigger not in {
            AlertTrigger.SCHEDULED_0945,
            AlertTrigger.SCHEDULED_1445,
        }:
            return
        self._submit_outcome_task(
            f"record:{alert_id}",
            lambda: tracker.record_scheduled_batch(
                batch,
                snapshot_id=snapshot_id,
                alert_id=alert_id,
                trigger_type=trigger.value,
                recorded_at=recorded_at,
            ),
        )

    def _secret_getter(self) -> str | None:
        if self._secrets is None:
            return None
        try:
            return self._secrets.active_token()
        except Exception:
            return None

    def _start_universe_refresh(self, now: datetime) -> bool:
        runtime = self._runtime
        # ``universe is None`` is the cold-start state for a Web volume with
        # no packaged desktop seed. It must be prepared here; treating it as
        # ineligible made every first Web scan skip forever.
        if runtime is None:
            return False
        if self._universe_retry_at is not None and now < self._universe_retry_at:
            return False
        try:
            logger.info("refreshing runtime universe cache")
            runtime.prepare(force_refresh=True)
            self._universe_retry_at = None
            self.status_issues = tuple(
                dict.fromkeys(
                    (
                        *self.status_issues,
                        "基础缓存已刷新。",
                    )
                )
            )
            logger.info("runtime universe cache refreshed")
            return True
        except ProviderError as error:
            self._universe_retry_at = now + timedelta(seconds=self.config.universe_retry_seconds)
            detail = f"基础缓存刷新失败：{error.reason.value}；将在稍后重试。"
            self.last_fetch_detail = detail
            self.health_detail = detail
            self.status_issues = tuple(dict.fromkeys((*self.status_issues, detail)))
            logger.warning("runtime universe refresh failed: %s", error.reason.value)
            return False
        except Exception as error:
            self._universe_retry_at = now + timedelta(seconds=self.config.universe_retry_seconds)
            detail = f"基础缓存刷新失败：{type(error).__name__}；将在稍后重试。"
            self.last_fetch_detail = detail
            self.health_detail = detail
            self.status_issues = tuple(
                dict.fromkeys(
                    (
                        *self.status_issues,
                        detail,
                    )
                )
            )
            logger.warning("runtime universe refresh failed: %s", type(error).__name__)
            return False

    def _universe_is_current(self, now: datetime) -> bool:
        runtime = self._runtime
        return (
            runtime is not None
            and runtime.universe is not None
            and runtime.universe_is_current(now)
        )

    def _universe_is_usable(self, now: datetime) -> bool:
        runtime = self._runtime
        return (
            runtime is not None
            and runtime.universe is not None
            and runtime.universe_is_usable(now)
        )

    def _session_is_trading(self, now: datetime) -> bool:
        runtime = self._runtime
        open_dates = runtime.universe.open_dates if runtime is not None and runtime.universe else ()
        if self._schedule.is_trading(now, open_dates):
            return True
        current = now.timetz().replace(tzinfo=None)
        return time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0)

    def tick(self, *, now: datetime | None = None) -> TickResult:
        """One worker iteration: tasks, summary, commands, bounded scan."""
        current = _shanghai(now or self._clock())
        if not self._scan_lock.acquire(blocking=False):
            return TickResult(skipped_reason="scan-in-progress")
        try:
            return self._tick_locked(current)
        finally:
            self._scan_lock.release()

    def _tick_locked(self, now: datetime) -> TickResult:
        self._prune_history_if_due(now)
        self._backfill_repeat_occurrences()
        self._detect_scan_stall(now)
        due_tasks = self._prepare_automation_tasks(now)
        summary_task = next(
            (spec for spec in due_tasks if spec.task_type is AutomationTaskType.SUMMARY_1530),
            None,
        )
        fixed_task = next(
            (
                spec
                for spec in due_tasks
                if spec.task_type
                in {AutomationTaskType.FIXED_0945, AutomationTaskType.FIXED_1445}
            ),
            None,
        )
        requested_trigger = (
            fixed_task.task_type.value
            if fixed_task is not None
            else "automatic"
        )
        if summary_task is not None:
            self._execute_summary_task(now, summary_task)
        if not self._ensure_runtime(now):
            self._record_scan_skip(
                now,
                trigger_type=requested_trigger,
                detail="运行环境初始化失败，本轮未发起实时请求。",
                health=HealthState.WARMING,
                task_key=fixed_task.task_key if fixed_task else None,
            )
            if fixed_task is not None:
                self._mark_task(
                    fixed_task.task_key,
                    state=AutomationTaskState.FAILED,
                    now=now,
                    detail="运行环境初始化失败。",
                )
            self._refresh_public_state(now)
            return TickResult(skipped_reason="runtime-init-failed")
        if self.config.settings.mode is not DataSourceMode.TUSHARE_15000:
            self.state = HealthState.WARMING
            self._record_scan_skip(
                now,
                trigger_type=requested_trigger,
                detail="未选择生产数据路线；等待配置。",
                health=HealthState.WARMING,
                task_key=fixed_task.task_key if fixed_task else None,
            )
            self._refresh_public_state(now)
            return TickResult(skipped_reason="mode-not-production")
        if self._secret_getter() is None:
            self._record_scan_skip(
                now,
                trigger_type=requested_trigger,
                detail="Token 未配置；等待 Admin 在 HTTPS 页面输入。",
                health=HealthState.WARMING,
                task_key=fixed_task.task_key if fixed_task else None,
            )
            self._refresh_public_state(now)
            return TickResult(skipped_reason="credential-missing")
        self._submit_initial_outcome_backfill(now)
        self._submit_unresolved_outcome_targets(now)
        runtime = self._runtime
        assert runtime is not None
        universe_current = self._universe_is_current(now)
        if not universe_current:
            self._start_universe_refresh(now)
            if not self._universe_is_usable(now):
                self._record_scan_skip(
                    now,
                    trigger_type=requested_trigger,
                    detail=(
                        self.last_fetch_detail
                        if self.last_fetch_detail.startswith("基础缓存刷新失败：")
                        else "基础缓存未准备完成，本轮未发起实时请求。"
                    ),
                    health=HealthState.WARMING,
                    task_key=fixed_task.task_key if fixed_task else None,
                )
                self._refresh_public_state(now)
                return TickResult(skipped_reason="universe-warming")
        if fixed_task is None and not self._session_is_trading(now):
            self._submit_due_outcome_backfills(now)
            self.state = HealthState.WARMING
            self.health_detail = "非交易时段不发起全市场实时扫描。"
            self._refresh_public_state(now)
            return TickResult(skipped_reason="not-trading")
        if fixed_task is not None:
            self._mark_task(
                fixed_task.task_key,
                state=AutomationTaskState.RUNNING,
                now=now,
                detail="高优先级任务已开始。",
                increment_attempt=True,
            )
        self.last_fetch_at = now
        attempt_id = self._begin_scan_attempt(now=now, operation=requested_trigger)
        outcome = runtime.scan_once()
        completed_at = _shanghai(self._clock())
        self._finish_scan_attempt(
            attempt_id,
            completed_at=completed_at,
            state=(
                "completed"
                if outcome.health is not HealthState.STOPPED
                else "failed"
            ),
            detail=outcome.failure_reason or outcome.detail,
        )
        self.state = outcome.health
        self.health_detail = outcome.detail
        self.last_fetch_detail = outcome.detail
        if outcome.failure_reason is not None:
            if not self._failure_active:
                runtime.reset_for_external_recovery()
            self._failure_active = True
        elif outcome.health is HealthState.HEALTHY:
            self._failure_active = False
        if outcome.batch is not None:
            self.batch = outcome.batch
        if outcome.health is HealthState.HEALTHY:
            self.last_scan_succeeded_at = completed_at
            self._recovery_round += 1
            if self._recovery_round >= 3:
                self._recovery_round = 0
        scan_run_id = self._record_scan_run(
            outcome,
            started_at=now,
            completed_at=completed_at,
            trigger_type=requested_trigger,
            task_key=fixed_task.task_key if fixed_task is not None else None,
        )
        crossed = (
            None
            if fixed_task is not None
            else self._schedule.crossed_fixed_trigger(now, completed_at)
        )
        snapshot_id = (
            self._persist_scan_snapshot(completed_at, source_type=requested_trigger)
            if outcome.health is HealthState.HEALTHY and outcome.batch is not None
            else None
        )
        fixed_trigger = (
            AlertTrigger(fixed_task.task_type.value)
            if fixed_task is not None
            else crossed
        )
        self._submit_fixed_outcome_settlement(
            completed_at,
            fixed_trigger,
            outcome.quotes,
        )
        alert_snapshot_id = self._evaluate_alerts(
            completed_at,
            outcome.strong_event,
            forced_fixed=fixed_trigger,
            selection_audit=outcome.selection_audit,
            scan_run_id=scan_run_id,
            snapshot_id=snapshot_id,
        )
        snapshot_id = alert_snapshot_id or snapshot_id
        if fixed_task is not None:
            existing_alert = next(
                (
                    row
                    for row in self._today_alerts(completed_at)
                    if row.get("trigger_type") == fixed_task.task_type.value
                ),
                None,
            )
            if existing_alert is not None:
                existing_snapshot_id = existing_alert.get("snapshot_id")
                completed_snapshot_id = (
                    existing_snapshot_id
                    if isinstance(existing_snapshot_id, int)
                    else None
                )
                self._mark_task(
                    fixed_task.task_key,
                    state=AutomationTaskState.SUCCEEDED,
                    now=completed_at,
                    detail=(
                        "任务已完成。"
                        if completed_snapshot_id is not None
                        else "提醒已在跨界扫描中发出。"
                    ),
                    snapshot_id=completed_snapshot_id,
                )
            elif snapshot_id is not None:
                self._mark_task(
                    fixed_task.task_key,
                    state=AutomationTaskState.SUCCEEDED,
                    now=completed_at,
                    detail="任务已完成。",
                    snapshot_id=snapshot_id,
                )
            else:
                self._mark_task(
                    fixed_task.task_key,
                    state=AutomationTaskState.FAILED,
                    now=completed_at,
                    detail=outcome.detail,
                )
        if fixed_trigger is None:
            self._submit_due_outcome_backfills(completed_at)
        self._refresh_public_state(now)
        return TickResult(
            scanned=True,
            snapshot_id=snapshot_id,
            skipped_reason=None,
        )

    def _detect_scan_stall(self, now: datetime) -> None:
        """Surface and record a stalled scan attempt; never block the loop."""
        if self._active_scan_attempt_id is None:
            return
        attempt = next(
            (
                row
                for row in self.store.list_scan_attempts(session_id=self._runtime_session_id)
                if row["attempt_id"] == self._active_scan_attempt_id
            ),
            None,
        )
        if attempt is None or attempt["completed_at"] is not None:
            return
        started = _parsed_datetime(attempt.get("started_at"))
        if started is None:
            return
        if (now - started).total_seconds() <= self.config.stall_threshold_seconds:
            return
        try:
            self.store.record_runtime_event(
                session_id=self._runtime_session_id,
                occurred_at=now.isoformat(),
                event_type="scan_stalled",
                detail={"attempt_id": attempt["attempt_id"]},
            )
        except Exception:
            pass

    # -- alerts ----------------------------------------------------------

    def _evaluate_alerts(
        self,
        now: datetime,
        strong_event: StrongMovementEvent | None,
        *,
        forced_fixed: AlertTrigger | None = None,
        selection_audit: object | None = None,
        scan_run_id: int | None = None,
        snapshot_id: int | None = None,
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
                existing_snapshot_id = existing.get("snapshot_id")
                return existing_snapshot_id if isinstance(existing_snapshot_id, int) else None
            if self.state is not HealthState.HEALTHY:
                return None
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
                    snapshot_id=snapshot_id,
                )
            return None
        if strong_event is None:
            return None
        audit = selection_audit
        velocity_ready = (
            bool(getattr(audit, "display_velocity_ready", False))
            if audit is not None
            else False
        )
        if not velocity_ready:
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
            subtitle = (
                "个股与板块同步增强｜资金未确认"
                if strong_event.funds_unconfirmed
                else "个股、板块与资金同步增强"
            )
            detail = self._build_strong_alert_detail(
                now,
                strong_event,
                audit,
                decision.reason,
                scan_run_id,
            )
            return self._record_alert(
                now,
                AlertTrigger.INTRADAY,
                decision.reason,
                "盘中强异动",
                subtitle,
                detail=detail,
                snapshot_id=snapshot_id,
            )
        return None

    def _build_strong_alert_detail(
        self,
        now: datetime,
        strong_event: StrongMovementEvent,
        audit: object | None,
        decision_reason: str,
        scan_run_id: int | None,
    ) -> dict[str, object]:
        codes = tuple(strong_event.triggering_codes)
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
            "velocity_1m_before": None,
            "sector_name": getattr(row, "sector", ""),
            "sector_type": getattr(row, "sector_type", ""),
            "feature_readiness": "ready",
            "cooldown_decision": decision_reason,
            "source_scan_id": scan_run_id,
        }
        return {key: value for key, value in detail.items() if value is not None}

    def _record_alert(
        self,
        now: datetime,
        trigger: AlertTrigger,
        decision: str,
        title: str,
        subtitle: str,
        *,
        detail: dict[str, object] | None = None,
        snapshot_id: int | None = None,
    ) -> int:
        assert self.batch is not None
        alert_detail = {
            **(detail or {}),
            "title": title,
            "subtitle": subtitle,
            "delayed": self.state is not HealthState.HEALTHY,
        }
        created_snapshot = snapshot_id is None
        with self.store.transaction() as connection:
            if snapshot_id is None:
                snapshot_id = self.store.record_batch_in(connection, self.batch)
                if self.state is HealthState.HEALTHY:
                    self._repeat_tracker.observe_batch_in(
                        connection,
                        batch=self.batch,
                        snapshot_id=snapshot_id,
                        seen_at=now,
                        source_type=trigger.value,
                    )
            else:
                self._repeat_tracker.note_source_in(
                    connection,
                    batch=self.batch,
                    snapshot_id=snapshot_id,
                    seen_at=now,
                    source_type=trigger.value,
                )
            self._snapshot_id = snapshot_id
            alert_id = self.store.record_alert_event_in(
                connection,
                snapshot_id=snapshot_id,
                displayed_at=now.isoformat(),
                decision=decision,
                channel="web-worker",
                trigger_type=trigger.value,
                detail=alert_detail,
            )
            if trigger.value in CandidateOutcomeTracker.scheduled_triggers:
                outcome_entries = (
                    CandidateOutcomeTracker.pending_entries_for_scheduled_batch(
                        self.batch,
                        snapshot_id=snapshot_id,
                        alert_id=alert_id,
                        trigger_type=trigger.value,
                        recorded_at=now,
                    )
                )
                self.store.create_candidate_outcomes_in(connection, outcome_entries)
            candidates = self._candidate_payload(self.batch, connection=connection)
            triggering_codes = (
                [str(detail["trigger_symbol"])]
                if detail and detail.get("trigger_symbol")
                else []
            )
            event_id = self._outbox.append(
                connection,
                event_type="alert.created",
                payload={
                    "alert_id": alert_id,
                    "snapshot_id": snapshot_id,
                    "trigger_type": trigger.value,
                    "displayed_at": now.isoformat(),
                    "triggering_codes": triggering_codes,
                    "strength": None,
                    "funds_unconfirmed": (
                        alert_detail.get("funds_unconfirmed")
                    ),
                    "candidates": candidates,
                },
                source_kind="alert",
                source_id=str(alert_id),
            )
            state_version = self._bump_state_version()
            self.store.upsert_public_state(
                connection,
                state_version=state_version,
                snapshot_id=snapshot_id,
                source_ts=self.batch.source_ts.isoformat(),
                payload=self._public_payload(now, event_id=event_id, connection=connection),
            )
        if created_snapshot:
            self._emit(
                event_type="candidates.updated",
                payload={
                    "snapshot_id": snapshot_id,
                    "state_version": state_version,
                    "source_ts": self.batch.source_ts.isoformat(),
                    "overall_weak": self.batch.overall_weak,
                    "candidates": candidates,
                },
                source_kind="snapshot",
                source_id=str(snapshot_id),
            )
        self._submit_scheduled_outcome_record(
            batch=self.batch,
            snapshot_id=snapshot_id,
            alert_id=alert_id,
            trigger=trigger,
            recorded_at=now,
        )
        return snapshot_id

    def _persist_scan_snapshot(
        self,
        now: datetime,
        *,
        source_type: str = "automatic",
    ) -> int | None:
        """Persist every healthy automatic result for factor-level audit."""
        if self.batch is None or len(self.batch.candidates) != 3:
            return None
        with self.store.transaction() as connection:
            snapshot_id = self.store.record_batch_in(connection, self.batch)
            self._snapshot_id = snapshot_id
            self._repeat_tracker.observe_batch_in(
                connection,
                batch=self.batch,
                snapshot_id=snapshot_id,
                seen_at=now,
                source_type=source_type,
            )
            state_version = self._bump_state_version()
            event_id = self._outbox.append(
                connection,
                event_type="candidates.updated",
                payload={
                    "snapshot_id": snapshot_id,
                    "state_version": state_version,
                    "source_ts": self.batch.source_ts.isoformat(),
                    "overall_weak": self.batch.overall_weak,
                    "candidates": self._candidate_payload(self.batch, connection=connection),
                },
                source_kind="snapshot",
                source_id=str(snapshot_id),
            )
            self.store.upsert_public_state(
                connection,
                state_version=state_version,
                snapshot_id=snapshot_id,
                source_ts=self.batch.source_ts.isoformat(),
                payload=self._public_payload(now, event_id=event_id, connection=connection),
            )
        return snapshot_id

    def _candidate_payload(
        self,
        batch: CandidateBatch,
        *,
        connection: Any | None = None,
    ) -> list[dict[str, Any]]:
        codes = [candidate.code for candidate in batch.candidates]
        if connection is not None:
            projections = self._repeat_tracker.projections_for_codes(connection, codes)
        else:
            projections = self._repeat_tracker.projections_from_store(codes)
        return [
            {
                "rank": index,
                "code": candidate.code,
                "name": candidate.name,
                "level": candidate.level,
                "is_formal": candidate.is_formal,
                "is_supplement": candidate.is_supplement,
                "price": candidate.price,
                "change_pct": candidate.change_pct,
                "sector_name": candidate.sector,
                "sector_type": getattr(candidate, "sector_type", "industry"),
                "total_score": candidate.total_score,
                "source_ts": batch.source_ts.isoformat(),
                **projections.get(candidate.code, RepeatProjection()).as_fields(),
            }
            for index, candidate in enumerate(batch.candidates, start=1)
        ]

    # -- scan evidence ---------------------------------------------------

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

    def _begin_scan_attempt(self, *, now: datetime, operation: str) -> str:
        attempt_id = uuid.uuid4().hex
        self._active_scan_attempt_id = attempt_id
        try:
            self.store.start_scan_attempt(
                attempt_id=attempt_id,
                session_id=self._runtime_session_id,
                started_at=now.isoformat(),
                operation=operation,
                thread_name="worker-scan",
                timer_active=True,
            )
        except Exception:
            pass
        return attempt_id

    def _finish_scan_attempt(
        self,
        attempt_id: str,
        *,
        completed_at: datetime,
        state: str,
        detail: str,
    ) -> None:
        self._active_scan_attempt_id = None
        try:
            self.store.finish_scan_attempt(
                attempt_id,
                completed_at.isoformat(),
                state=state,
                detail=detail,
            )
        except Exception:
            pass

    # -- summary ---------------------------------------------------------

    def _execute_summary_task(
        self,
        now: datetime,
        spec: AutomationTaskSpec,
        *,
        catch_up: bool = False,
    ) -> bool:
        if self._summary_retry_at is not None and now < self._summary_retry_at:
            return False
        self._mark_task(
            spec.task_key,
            state=AutomationTaskState.RUNNING,
            now=now,
            detail="15:30总结开始。",
            increment_attempt=True,
        )
        if self.generate_summary(now, catch_up=catch_up):
            self._mark_task(
                spec.task_key,
                state=AutomationTaskState.SUCCEEDED,
                now=now,
                detail=(
                    "15:30补生成总结（catch_up=true）。"
                    if catch_up
                    else "15:30本地总结已生成；外部增强可降级。"
                ),
            )
            return True
        self._mark_task(
            spec.task_key,
            state=AutomationTaskState.FAILED,
            now=now,
            detail=self._summary_issue or "盘后总结生成失败。",
        )
        return False

    def generate_summary(self, now: datetime, *, catch_up: bool = False) -> bool:
        """15:30 summary with local-first fallback (desktop-equivalent semantics)."""
        trade_date = now.date().isoformat()
        if self._summary_date == trade_date:
            return True
        existing_summary = self.store.get_daily_summary(trade_date)
        if existing_summary is not None:
            reports_dir = self._reports_dir()
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
                    self._emit_summary_ready(trade_date, "full_market")
                    return True
                except Exception:
                    logger.warning(
                        "post-close summary failed stage=full_json_render",
                        exc_info=True,
                    )
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
                        self._emit_summary_ready(trade_date, "local_fallback")
                        return True
                except Exception:
                    logger.warning(
                        "post-close summary failed stage=local_json_reuse",
                        exc_info=True,
                    )
            try:
                self._write_local_summary_report(existing_summary)
            except Exception:
                logger.warning(
                    "post-close summary failed stage=local_report_write",
                    exc_info=True,
                )
                self._set_summary_retry(now)
                return False
            self._summary_date = trade_date
            self._emit_summary_ready(trade_date, "local_fallback")
            return True
        if self._summary_retry_at is not None and now < self._summary_retry_at:
            return False
        history = [
            row
            for row in self.store.list_alert_history(now=now, days=1)
            if str(row.get("displayed_at", "")).startswith(trade_date)
        ]
        interruption_count = self.store.count_health_interruptions(trade_date)
        collection = None
        if self._provider is not None:
            try:
                collection = collect_post_close_review(
                    self._provider,
                    trade_date=now.date(),
                    generated_at=now,
                )
            except Exception:
                logger.warning(
                    "post-close summary failed stage=provider_collection",
                    exc_info=True,
                )
                collection = None
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
                    reports_dir=self._reports_dir(),
                    alert_count=len(history),
                    health_interruption_count=interruption_count,
                    alert_timeline=alert_timeline_records(history),
                )
            except Exception:
                logger.warning(
                    "post-close summary failed stage=full_market_write",
                    exc_info=True,
                )
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
                logger.warning(
                    "post-close summary failed stage=local_report_write",
                    exc_info=True,
                )
                self._set_summary_retry(now)
                return False
        self.store.prune_daily_summaries(before=now.date() - timedelta(days=30))
        self._summary_date = trade_date
        self._summary_retry_at = None
        self._summary_issue = None
        self._emit_summary_ready(
            trade_date,
            "full_market" if collection is not None else "local_fallback",
        )
        return True

    def _emit_summary_ready(self, trade_date: str, report_mode: str) -> None:
        reports_dir = self._reports_dir()
        pdf = reports_dir / f"{trade_date}-A股盘后回顾.pdf"
        self._emit(
            event_type="summary.ready",
            payload={
                "trade_date": trade_date,
                "report_mode": report_mode,
                "pdf_available": pdf.is_file(),
                "catch_up": False,
                "source_commit": self.config.source_commit,
            },
            source_kind="summary",
            source_id=trade_date,
        )

    def _reports_dir(self) -> Path:
        return self.config.report_dir or report_directory_for_database(self.store.path)

    def _collect_continuity_evidence(self, trade_date: str) -> str:
        runs = [
            row
            for row in self.store.list_scan_runs(trade_date)
            if row.get("completed_at") and row.get("health") == HealthState.HEALTHY.value
        ]
        timestamps: list[datetime] = []
        for row in runs:
            parsed = _parsed_datetime(str(row["completed_at"]))
            if parsed is not None:
                timestamps.append(parsed)
        timestamps.sort()
        gaps: list[tuple[float, datetime, datetime]] = []
        for previous, current in zip(timestamps, timestamps[1:]):
            delta = (current - previous).total_seconds()
            if delta > 0:
                gaps.append((delta, previous, current))
        parts: list[str] = []
        if gaps:
            longest, gap_start, gap_end = max(gaps)
            trading = _trading_block(gap_start) == _trading_block(gap_end) > 0
            parts.append(
                f"最长无扫描间隔{_format_duration(longest)}（{gap_start:%H:%M}→{gap_end:%H:%M}"
                f"{'，位于交易时段内' if trading else '，位于非交易时段'}）"
            )
        sessions = self.store.list_runtime_sessions(trade_date)
        if len(sessions) > 1:
            parts.append(f"进程重启{len(sessions) - 1}次")
        event_counts: dict[str, int] = {}
        for session in sessions:
            for event in self.store.list_runtime_events(str(session["session_id"])):
                occurred = str(event.get("occurred_at", ""))
                if not occurred.startswith(trade_date):
                    continue
                event_type = str(event.get("event_type", ""))
                event_counts[event_type] = event_counts.get(event_type, 0) + 1
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
            else "未加载（使用行业上下文）"
        )
        parts.append(f"概念缓存：{concept_state}")
        return "；".join(parts) if parts else "未记录到扫描或连续性事件。"

    def _local_summary_observations(self, trade_date: str) -> list[dict[str, object]]:
        rows = [
            row
            for row in self.store.list_scan_runs(trade_date)
            if row.get("health") == HealthState.HEALTHY.value and row.get("stable_batch_json")
        ]
        return [{"payload_json": str(row["stable_batch_json"])} for row in rows[-30:]]

    def _write_local_summary_report(self, summary: dict[str, object]) -> None:
        generated_at = _parsed_datetime(str(summary.get("generated_at", ""))) or _shanghai(
            self._clock()
        )
        write_local_fallback_artifacts(
            self.store,
            summary,
            reports_dir=self._reports_dir(),
            now=generated_at,
            source_commit_value=self.config.source_commit,
        )

    def _set_summary_retry(self, now: datetime) -> None:
        self._summary_retry_at = now + timedelta(seconds=60)
        self._summary_issue = "盘后回顾暂未生成，将在60秒后自动重试。"
        self.status_issues = tuple(dict.fromkeys((*self.status_issues, self._summary_issue)))

    # -- commands --------------------------------------------------------

    def handle_command(self, command: dict[str, Any]) -> None:
        """Execute one claimed durable command (called by the Worker)."""
        self._command_context.command = command
        try:
            command_type = CommandType(str(command["command_type"]))
            command_id = str(command["command_id"])
            if command_type is CommandType.MANUAL_REFRESH:
                self._manual_refresh(command)
            elif command_type is CommandType.UNIVERSE_REFRESH:
                self._universe_refresh(command)
            elif command_type is CommandType.SUMMARY_GENERATE:
                self._summary_generate(command)
            elif command_type in {CommandType.TOKEN_TEST, CommandType.TOKEN_UPDATE}:
                self._token_command(command, update=command_type is CommandType.TOKEN_UPDATE)
            else:
                self._finish_command(command_id, CommandStatus.FAILED, error_code="unsupported")
        finally:
            self._command_context.command = None

    def _manual_refresh(self, command: dict[str, Any]) -> None:
        with self._scan_lock:
            self._manual_refresh_locked(command)

    def _manual_refresh_locked(self, command: dict[str, Any]) -> None:
        command_id = str(command["command_id"])
        started = monotonic_time()
        deadline = started + self.config.manual_timeout_seconds
        self._ensure_runtime(_shanghai(self._clock()))
        scan_round = 0
        result: dict[str, Any] = {"rounds": 0}
        while True:
            now = _shanghai(self._clock())
            if self._universe_is_usable(now):
                scan_round += 1
            outcome = self._tick_scan(now, force=True, manual_request=True)
            if outcome is not None and outcome.health is HealthState.HEALTHY:
                self.state = outcome.health
            if (
                outcome is not None
                and outcome.health is HealthState.HEALTHY
                and self.batch is not None
                and len(self.batch.candidates) == 3
            ):
                if self._manual_result_ready(outcome, deadline=deadline, round_no=scan_round):
                    result["rounds"] = scan_round
                    result["snapshot_id"] = self._persist_manual_snapshot(now)
                    self._finish_command(command_id, CommandStatus.SUCCEEDED, result=result)
                    return
            if outcome is not None and outcome.health is HealthState.STOPPED:
                break
            if self._secret_getter() is None:
                break
            if not self._manual_should_wait():
                break
            if monotonic_time() >= deadline:
                break
            sleep_seconds(1.0)
        error_code = "timeout"
        error_detail = "本次获取未在安全时限内完成；未产生新候选。"
        if self._secret_getter() is None:
            error_code = "credential-missing"
            error_detail = "Token 未配置；未产生新候选。"
        elif self.last_fetch_detail.startswith("基础缓存刷新失败："):
            error_code = "universe-refresh"
            error_detail = self.last_fetch_detail
        self._finish_command(
            command_id,
            CommandStatus.FAILED,
            error_code=error_code,
            error_detail=error_detail,
        )

    def _tick_scan(
        self,
        now: datetime,
        *,
        force: bool,
        manual_request: bool,
    ) -> ScanOutcome | None:
        if not self._ensure_runtime(now):
            return None
        if self._secret_getter() is None:
            return None
        if not self._universe_is_current(now):
            self._start_universe_refresh(now)
            if not self._universe_is_usable(now):
                return None
        if not force and not self._session_is_trading(now):
            return None
        runtime = self._runtime
        assert runtime is not None
        attempt_id = self._begin_scan_attempt(
            now=now,
            operation="manual" if manual_request else "automatic",
        )
        outcome = runtime.scan_once()
        completed_at = _shanghai(self._clock())
        self._finish_scan_attempt(
            attempt_id,
            completed_at=completed_at,
            state=(
                "completed"
                if outcome.health is not HealthState.STOPPED
                else "failed"
            ),
            detail=outcome.failure_reason or outcome.detail,
        )
        self.state = outcome.health
        self.health_detail = outcome.detail
        if outcome.failure_reason is not None:
            if not self._failure_active:
                runtime.reset_for_external_recovery()
            self._failure_active = True
        elif outcome.health is HealthState.HEALTHY:
            self._failure_active = False
        if outcome.batch is not None:
            self.batch = outcome.batch
        if outcome.health is HealthState.HEALTHY:
            self.last_scan_succeeded_at = completed_at
        self._record_scan_run(
            outcome,
            started_at=now,
            completed_at=completed_at,
            trigger_type="manual" if manual_request else "automatic",
            task_key=None,
        )
        return outcome

    def _manual_result_ready(
        self,
        outcome: ScanOutcome,
        *,
        deadline: float,
        round_no: int,
    ) -> bool:
        audit = outcome.selection_audit
        if audit is not None and getattr(audit, "display_velocity_ready", False):
            return True
        if round_no >= self._manual_required_scan_cycles():
            return True
        return monotonic_time() >= deadline

    def _manual_required_scan_cycles(self) -> int:
        runtime = self._runtime
        if runtime is not None:
            required = getattr(runtime, "health", None)
            if isinstance(required, DataHealthTracker):
                return max(1, required.required_cycles)
        return max(1, DataHealthConfig().initial_cycles)

    def _manual_should_wait(self) -> bool:
        if self._runtime is None:
            return True
        return not self._universe_is_usable(_shanghai(self._clock()))

    def _persist_manual_snapshot(self, now: datetime) -> int | None:
        """Manual observations are saved without an automatic alert event."""
        if self.batch is None or len(self.batch.candidates) != 3:
            return None
        with self.store.transaction() as connection:
            snapshot_id = self.store.record_batch_in(connection, self.batch)
            self._snapshot_id = snapshot_id
            if self.state is HealthState.HEALTHY:
                self._repeat_tracker.observe_batch_in(
                    connection,
                    batch=self.batch,
                    snapshot_id=snapshot_id,
                    seen_at=now,
                    source_type="manual",
                )
            state_version = self._bump_state_version()
            candidates = self._candidate_payload(self.batch, connection=connection)
            self.store.upsert_public_state(
                connection,
                state_version=state_version,
                snapshot_id=snapshot_id,
                source_ts=self.batch.source_ts.isoformat(),
                payload=self._public_payload(now, connection=connection),
            )
        self._emit(
            event_type="candidates.updated",
            payload={
                "snapshot_id": snapshot_id,
                "state_version": state_version,
                "source_ts": self.batch.source_ts.isoformat(),
                "overall_weak": self.batch.overall_weak,
                "candidates": candidates,
            },
            source_kind="snapshot",
            source_id=str(snapshot_id),
        )
        return snapshot_id

    def _universe_refresh(self, command: dict[str, Any]) -> None:
        command_id = str(command["command_id"])
        self._ensure_runtime(_shanghai(self._clock()))
        ok = self._start_universe_refresh(_shanghai(self._clock()))
        self._finish_command(
            command_id,
            CommandStatus.SUCCEEDED if ok else CommandStatus.FAILED,
            result={"refreshed": ok},
            error_code=None if ok else "universe_cache",
        )

    def _summary_generate(self, command: dict[str, Any]) -> None:
        command_id = str(command["command_id"])
        trade_date = str(command.get("payload", {}).get("trade_date") or "")
        if not trade_date:
            self._finish_command(command_id, CommandStatus.FAILED, error_code="invalid-payload")
            return
        try:
            target = datetime.fromisoformat(trade_date).replace(
                hour=15, minute=30, tzinfo=SHANGHAI
            )
        except ValueError:
            self._finish_command(command_id, CommandStatus.FAILED, error_code="invalid-payload")
            return
        ok = self.generate_summary(target, catch_up=True)
        self._finish_command(
            command_id,
            CommandStatus.SUCCEEDED if ok else CommandStatus.FAILED,
            result={"trade_date": trade_date, "catch_up": True},
            error_code=None if ok else "summary-failed",
        )

    def _token_command(self, command: dict[str, Any], *, update: bool) -> None:
        command_id = str(command["command_id"])
        if self._secrets is None:
            self._finish_command(command_id, CommandStatus.FAILED, error_code="secrets-unavailable")
            return
        request_id = str(command.get("secret_request_id") or "")
        try:
            token, purpose = self._secrets.consume_request(request_id)
        except Exception as error:
            self._finish_command(
                command_id,
                CommandStatus.FAILED,
                error_code=type(error).__name__,
                error_detail="secret request unavailable",
            )
            return
        capability = self._probe_token(token)
        ok = capability.get("ok") is True
        if not ok:
            try:
                self._secrets.fail_request(request_id)
            except Exception:
                pass
            diagnostic = capability.get("diagnostic")
            safe_result = (
                {"diagnostic": diagnostic}
                if isinstance(diagnostic, dict)
                else None
            )
            self._finish_command(
                command_id,
                CommandStatus.FAILED,
                result=safe_result,
                error_code=str(capability.get("error_code") or "probe-failed"),
                error_detail=str(capability.get("error") or "分层探测失败"),
            )
            return
        if update:
            try:
                self._secrets.store_active(
                    token=token,
                    capability=capability,
                )
                self._reset_runtime_for_token()
            except Exception as error:
                self._finish_command(
                    command_id,
                    CommandStatus.FAILED,
                    error_code=type(error).__name__,
                    error_detail="token activation failed; previous token retained",
                )
                return
        self._finish_command(
            command_id,
            CommandStatus.SUCCEEDED,
            result={
                "fingerprint": self._secrets.active_fingerprint(),
                "capability": capability,
                "activated": update,
            },
        )

    def _probe_token(self, token: str) -> dict[str, Any]:
        """Run the same low-cost activation gate as the desktop App.

        Token activation must not depend on stock-list, sector or native
        realtime availability. Those are real product capabilities, but they
        are checked by the normal Worker/runtime path after activation. Keeping
        this gate to ``trade_cal`` lets a valid Token enter the encrypted
        active slot even when one optional endpoint is rate-limited or not
        enabled for the account; the scan remains fail-closed until its own
        data gates pass.
        """
        settings = self.config.settings
        budget = ApplicationRequestBudget(settings.request_budget_interval_seconds)
        now = _shanghai(self._clock())
        result: dict[str, Any] = {
            "ok": False,
            "layers": [],
            "realtime_route": "native_realtime",
        }
        try:
            pro = TushareSdkProTransport(
                settings.primary_profile,
                lambda: token,
                request_budget=budget,
            )
            calendar = pro.execute(
                TransportRequest(
                    endpoint="/",
                    api_name="trade_cal",
                    params={
                        "exchange": "SSE",
                        "start_date": (now - timedelta(days=7)).strftime("%Y%m%d"),
                        "end_date": now.strftime("%Y%m%d"),
                    },
                    fields=("exchange", "cal_date", "is_open"),
                    allow_empty=True,
                )
            )
        except ProviderError as error:
            result["error_code"] = f"trade_calendar:{error.reason.value}"
            result["error"] = error.public_message
            result["diagnostic"] = {
                "reason": error.reason.value,
                "http_status": error.http_status,
            }
            return result
        except Exception as error:
            result["error_code"] = "trade_calendar:unexpected_error"
            result["error"] = type(error).__name__
            return result

        result["layers"].append(
            {
                "layer": "trade_calendar",
                "ok": True,
                "rows": len(calendar.records),
            }
        )
        result["ok"] = True
        return result

    def _reset_runtime_for_token(self) -> None:
        """Rebuild provider/runtime after activation; recovery gates reapply."""
        self._runtime = None
        self._provider = None
        self._outcome_tracker = None
        self._outcome_initial_backfill_done = False
        self._outcome_initial_backfill_retry_at = None
        self._prepared_date = None
        self.state = HealthState.WARMING
        self._recovery_round = 0

    def _finish_command(
        self,
        command_id: str,
        status: CommandStatus,
        *,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        holder_id = getattr(self, "_holder_id", "")
        fencing_token = getattr(self, "_fencing_token", 0)
        context_command = getattr(self._command_context, "command", None)
        if not isinstance(context_command, dict):
            return
        command: dict[str, Any] = context_command
        attempt_value = command.get("attempts")
        if not isinstance(attempt_value, int) or attempt_value < 1:
            return
        expected_attempt = attempt_value
        try:
            completed = self._commands.complete(
                command_id,
                holder_id=holder_id,
                fencing_token=fencing_token,
                expected_attempt=expected_attempt,
                status=status,
                result=result,
                error_code=error_code,
                error_detail=error_detail,
            )
        except Exception:
            completed = False
        if completed:
            self._emit(
                event_type="command.updated",
                payload={
                    "command_id": command_id,
                    "command_type": command.get("command_type", "unknown"),
                    "status": status.value,
                    "error_code": error_code,
                    "requested_by": command.get("requested_by"),
                    "attempts": expected_attempt,
                },
                source_kind="command",
                source_id=command_id,
            )

    # -- public state / events ------------------------------------------

    def _bump_state_version(self) -> int:
        self._state_version += 1
        return self._state_version

    def _public_payload(
        self,
        now: datetime,
        *,
        event_id: int | None = None,  # noqa: ARG002 - kept for outbox-aware callers
        connection: Any | None = None,
    ) -> dict[str, Any]:
        batch = self.batch
        return {
            "service_state": self.state.value.casefold(),
            "market_state": self._market_state_label(now),
            "state_version": self._state_version,
            "snapshot_id": self._snapshot_id,
            "candidates": (
                self._candidate_payload(batch, connection=connection)
                if batch is not None
                else []
            ),
            "overall_weak": bool(batch.overall_weak) if batch is not None else False,
            "source_ts": batch.source_ts.isoformat() if batch is not None else None,
            "fund_module": (
                getattr(batch, "fund_module", "unavailable")
                if batch is not None
                else "unavailable"
            ),
            "formal_count": (
                sum(1 for c in batch.candidates if c.is_formal)
                if batch is not None
                else 0
            ),
        }

    def _market_state_label(self, now: datetime) -> str:
        current = now.timetz().replace(tzinfo=None)
        if current < time(9, 30):
            return "preopen"
        if current <= time(11, 30):
            return "morning"
        if current < time(13, 0):
            return "lunch"
        if current <= time(15, 0):
            return "afternoon"
        return "closed"

    def _refresh_public_state(self, now: datetime) -> None:
        """Persist the projection (no snapshot change) and emit state.changed."""
        with self.store.transaction() as connection:
            state_version = self._bump_state_version()
            snapshot_id = self._snapshot_id
            self.store.upsert_public_state(
                connection,
                state_version=state_version,
                snapshot_id=snapshot_id,
                source_ts=self.batch.source_ts.isoformat() if self.batch is not None else None,
                payload=self._public_payload(now, connection=connection),
            )
        self._emit(
            event_type="state.changed",
            payload={"state_version": state_version, "changed_fields": ["service_state"]},
            source_kind="public_state",
            source_id=f"v{state_version}",
        )

    def _emit(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        source_kind: str | None = None,
        source_id: str | None = None,
        visibility: str = "all",
    ) -> int:
        try:
            return self._outbox.append_own(
                event_type=event_type,
                payload=payload,
                correlation_id=correlation_id,
                source_kind=source_kind,
                source_id=source_id,
                visibility=visibility,
            )
        except Exception:
            return 0

    def _backfill_repeat_occurrences(self) -> None:
        if self._repeat_backfill_done:
            return
        existing = self.store.get_app_setting("candidate_repeat_backfill_status")
        if (
            isinstance(existing, dict)
            and existing.get("status") == "completed"
            and existing.get("version") == REPEAT_BACKFILL_VERSION
        ):
            self._repeat_backfill_done = True
            return
        try:
            report = self._repeat_tracker.backfill()
            self.store.set_app_setting(
                "candidate_repeat_backfill_status",
                {
                    "status": "completed",
                    "version": REPEAT_BACKFILL_VERSION,
                    "snapshots": report.snapshots,
                    "occurrences": report.occurrences,
                    "activated": report.activated,
                    "skipped": report.skipped,
                },
            )
            self._repeat_backfill_done = True
        except Exception as error:  # noqa: BLE001 - sidecar must not fail the scan
            logger.warning("candidate repeat backfill failed: %s", type(error).__name__)

    def _prune_history_if_due(self, now: datetime) -> None:
        if self._history_pruned_date == now.date():
            return
        try:
            self.store.prune_history(before=now - timedelta(days=30))
        except Exception:
            self._history_prune_issue = "历史清理暂未完成，将在下次检查重试。"
            return
        self._history_pruned_date = now.date()
        self._history_prune_issue = None
