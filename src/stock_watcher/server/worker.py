"""Unique background Worker.

Owns the lease, the runtime, scans, automation, commands and the outbox.
A second instance (``docker compose up --scale worker=2``) fails the lease
and exits safely without ever scanning.
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time as time_module
from datetime import datetime

from stock_watcher.build_info import source_commit
from stock_watcher.domain import SHANGHAI
from stock_watcher.services import (
    CommandService,
    CommandStatus,
    EventOutbox,
    LeaseAcquireError,
    LeaseConfig,
    SecretService,
    WorkerLease,
)
from stock_watcher.services.secret_service import load_master_key
from stock_watcher.services.stockwatcher_service import StockWatcherService
from stock_watcher.storage import SQLiteStore

from .config import ServerSettings
from .redaction import redact

logger = logging.getLogger("stock_watcher.worker")

LEASE_NAME = "stockwatcher-worker"
HEARTBEAT_SECONDS = 5.0
TICK_INTERVAL_SECONDS = 10.0


class WorkerRuntime:
    """Lease-guarded worker main loop."""

    def __init__(self, settings: ServerSettings) -> None:
        self.settings = settings
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        settings.report_dir.mkdir(parents=True, exist_ok=True)
        self.store = SQLiteStore(settings.db_path)
        self.store.initialize()
        self.lease = WorkerLease(
            self.store,
            source_commit=(
                settings.source_commit
                if settings.source_commit != "unknown"
                else source_commit()
            ),
            config=LeaseConfig(lease_name=LEASE_NAME),
        )
        self.outbox = EventOutbox(
            self.store,
            source_commit=(
                settings.source_commit
                if settings.source_commit != "unknown"
                else source_commit()
            ),
        )
        self.commands = CommandService(self.store)
        master_key = load_master_key(settings.require_master_key())
        self.secrets = SecretService(
            self.store,
            master_key=master_key,
            environment=settings.environment,
            key_version=settings.secret_key_version,
        )
        self.service = StockWatcherService(
            self.store,
            config=None,
            outbox=self.outbox,
            commands=self.commands,
            secrets=self.secrets,
            source_commit=(
                settings.source_commit
                if settings.source_commit != "unknown"
                else source_commit()
            ),
            auto_start_session=False,
        )
        self._stop = threading.Event()
        self._lease_lost = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._runtime_heartbeat_thread: threading.Thread | None = None
        self._scan_thread: threading.Thread | None = None
        self._scan_thread_kind: str | None = None
        self._scan_thread_started_at: float | None = None
        self._scan_command: dict[str, object] | None = None
        self._scan_state_lock = threading.Lock()
        self._auto_cancel_requested = False
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_triggered = threading.Event()
        self._last_progress_event_at = 0.0
        self.service._holder_id = ""  # noqa: SLF001
        self.service._fencing_token = 0  # noqa: SLF001

    def _on_signal(self, signum: int, _frame: object) -> None:
        logger.info("received signal %s; shutting down", signum)
        self._stop.set()

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        try:
            self.lease.acquire()
        except LeaseAcquireError as error:
            logger.warning("safe exit without scanning: %s", redact(str(error)))
            return 0
        logger.info(
            "worker lease acquired: holder=%s fencing=%s",
            self.lease.holder_id,
            self.lease.fencing_token,
        )
        self.service._holder_id = self.lease.holder_id  # noqa: SLF001
        self.service._fencing_token = self.lease.fencing_token  # noqa: SLF001
        self.store.bind_write_guard(self.lease.assert_owned)
        self.service.start()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="worker-lease-heartbeat",
        )
        self._heartbeat_thread.start()
        self._runtime_heartbeat_thread = threading.Thread(
            target=self._runtime_heartbeat_loop,
            daemon=True,
            name="worker-runtime-heartbeat",
        )
        self._runtime_heartbeat_thread.start()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name="worker-scan-watchdog",
        )
        self._watchdog_thread.start()
        last_tick = 0.0
        last_maintenance = 0.0
        while not self._stop.is_set():
            try:
                self._record_loop_progress()
                now = time_module.monotonic()
                if now - last_tick >= TICK_INTERVAL_SECONDS:
                    self._tick()
                    last_tick = now
                if now - last_maintenance >= 60.0:
                    self._maintenance()
                    last_maintenance = now
                time_module.sleep(1.0)
            except Exception as error:
                logger.error("worker loop error: %s", redact(str(error)))
                if "lease" in str(error).casefold():
                    logger.error("lease lost; stopping business work")
                    break
        self._stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=HEARTBEAT_SECONDS + 1.0)
        if self._runtime_heartbeat_thread is not None:
            self._runtime_heartbeat_thread.join(timeout=HEARTBEAT_SECONDS + 1.0)
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(timeout=1.0)
        if self._lease_lost.is_set():
            # Do not enter a potentially blocked SQLite cleanup path after
            # fencing has failed.  The container restart policy will launch a
            # fresh Worker, and its next session will preserve the unclean
            # exit evidence before reacquiring the lease.
            logger.error("lease lost; exiting without blocking cleanup")
            return 1
        self.service.cancel_inflight()
        if self._scan_thread is not None:
            join_timeout = (
                self.settings.worker_watchdog_grace_seconds
                if self._watchdog_triggered.is_set()
                else self.lease.config.ttl_seconds / 2
            )
            self._scan_thread.join(timeout=join_timeout)
            if self._scan_thread.is_alive():
                logger.error("scan thread did not stop; preserving lease until expiry")
                return 1
        if self._watchdog_triggered.is_set():
            logger.error("worker watchdog triggered; exiting for container restart")
            return 1
        self.service.stop(exit_reason="worker_shutdown", graceful=True)
        try:
            self.lease.release()
        except Exception:
            pass
        logger.info("worker stopped cleanly")
        return 0

    def _heartbeat_loop(self) -> None:
        """Renew the fencing lease without any other potentially slow work."""
        while not self._stop.is_set():
            try:
                self.lease.renew()
            except Exception as error:
                logger.error("worker heartbeat failed: %s", redact(str(error)))
                self._lease_lost.set()
                self._stop.set()
                return
            if self._stop.wait(HEARTBEAT_SECONDS):
                return

    def _runtime_heartbeat_loop(self) -> None:
        """Persist runtime evidence without being able to starve lease renewal."""
        while not self._stop.is_set():
            try:
                self.service.heartbeat()
            except Exception as error:
                logger.warning(
                    "worker runtime heartbeat failed: %s",
                    redact(str(error)),
                )
            if self._stop.wait(HEARTBEAT_SECONDS):
                return

    def _record_worker_event(self, event_type: str, detail: dict[str, object]) -> None:
        """Persist process-progress evidence without ever logging secrets."""
        try:
            self.store.record_runtime_event(
                session_id=self.service._runtime_session_id,  # noqa: SLF001
                occurred_at=datetime.now(SHANGHAI).isoformat(),
                event_type=event_type,
                detail=detail,
            )
        except Exception as error:
            logger.warning("worker progress evidence failed: %s", redact(str(error)))

    def _record_loop_progress(self) -> None:
        now = time_module.monotonic()
        if now - self._last_progress_event_at < HEARTBEAT_SECONDS:
            return
        self._last_progress_event_at = now
        with self._scan_state_lock:
            scan_kind = self._scan_thread_kind
        self._record_worker_event("worker.loop", {"scan_kind": scan_kind})

    def _scan_state(self) -> tuple[threading.Thread | None, str | None, float | None]:
        with self._scan_state_lock:
            return (
                self._scan_thread,
                self._scan_thread_kind,
                self._scan_thread_started_at,
            )

    def _start_scan_thread(
        self,
        *,
        kind: str,
        target: object,
        args: tuple[object, ...] = (),
        command: dict[str, object] | None = None,
    ) -> None:
        if not callable(target):
            raise TypeError("scan target must be callable")
        thread = threading.Thread(
            target=target,
            args=args,
            daemon=True,
            name=f"worker-{kind}-scan",
        )
        started_at = time_module.monotonic()
        with self._scan_state_lock:
            self._scan_thread = thread
            self._scan_thread_kind = kind
            self._scan_thread_started_at = started_at
            self._scan_command = command
            self._auto_cancel_requested = False
        self._record_worker_event(
            "worker.scan_started",
            {"kind": kind},
        )
        thread.start()

    def _finish_scan_thread(self) -> None:
        current = threading.current_thread()
        with self._scan_state_lock:
            if self._scan_thread is not current:
                return
            kind = self._scan_thread_kind
            self._scan_thread = None
            self._scan_thread_kind = None
            self._scan_thread_started_at = None
            self._scan_command = None
            self._auto_cancel_requested = False
        self._record_worker_event("worker.scan_finished", {"kind": kind})

    def _fail_watchdog_command(self) -> None:
        with self._scan_state_lock:
            command = dict(self._scan_command) if self._scan_command is not None else None
        if command is None:
            return
        attempts = command.get("attempts")
        if not isinstance(attempts, int) or attempts < 1:
            return
        completed = self.commands.complete(
            str(command["command_id"]),
            holder_id=self.lease.holder_id,
            fencing_token=self.lease.fencing_token,
            expected_attempt=attempts,
            status=CommandStatus.FAILED,
            error_code="worker_watchdog_timeout",
            error_detail="Worker 扫描超过安全时限，已重启；本次未产生新候选。",
        )
        if completed:
            saved = self.commands.get(str(command["command_id"]))
            if saved is not None:
                self._emit_command(saved)

    def _watchdog_loop(self) -> None:
        while not self._stop.wait(1.0):
            thread, kind, started_at = self._scan_state()
            if thread is None or kind is None or started_at is None:
                continue
            elapsed = time_module.monotonic() - started_at
            if elapsed <= self.settings.worker_scan_timeout_seconds:
                continue
            logger.error(
                "worker scan watchdog fired: kind=%s elapsed=%.1fs",
                kind,
                elapsed,
            )
            self.service.cancel_inflight()
            self._fail_watchdog_command()
            self._record_worker_event(
                "worker.watchdog",
                {"kind": kind, "elapsed_seconds": round(elapsed, 1)},
            )
            self._watchdog_triggered.set()
            self._stop.set()
            deadline = time_module.monotonic() + self.settings.worker_watchdog_grace_seconds
            while thread.is_alive() and time_module.monotonic() < deadline:
                time_module.sleep(0.1)
            if thread.is_alive():
                # The Worker is a dedicated container process.  A provider or
                # database call that ignores cancellation must not leave the
                # lease and the readiness endpoint falsely healthy forever.
                os._exit(1)
            return

    def _tick(self) -> None:
        self.lease.renew()
        # Recover crashed/expired commands first so obligations never vanish.
        transitions = self.commands.expire_stale()
        for transition in transitions:
            self._emit_command(transition)
        scan_thread, scan_kind, _ = self._scan_state()
        if scan_thread is not None and scan_thread.is_alive():
            if scan_kind == "automatic" and not self._auto_cancel_requested:
                if self.commands.has_queued():
                    self.service.cancel_inflight()
                    self._auto_cancel_requested = True
                    logger.info("automatic scan cancelled for queued command")
            return
        claimed = self.commands.claim_next(
            holder_id=self.lease.holder_id,
            fencing_token=self.lease.fencing_token,
        )
        if claimed is not None:
            command = claimed
            self._emit_command(command)
            self._start_scan_thread(
                kind="command",
                target=self._run_command_safe,
                args=(command,),
                command=command,
            )
            return
        self._start_scan_thread(
            kind="automatic",
            target=self._run_auto_tick_safe,
        )

    def _run_auto_tick_safe(self) -> None:
        try:
            tick = self.service.tick()
            if tick.skipped_reason is not None:
                logger.debug("tick skipped: %s", tick.skipped_reason)
        except Exception as error:
            logger.error("automatic tick failed: %s", redact(str(error)))
        finally:
            self._finish_scan_thread()

    def _run_command_safe(self, command: dict[str, object]) -> None:
        try:
            self.service.handle_command(command)
        except Exception as error:
            logger.error("command %s failed: %s", command.get("command_id"), redact(str(error)))
            try:
                attempt = command.get("attempts")
                self.commands.complete(
                    str(command["command_id"]),
                    holder_id=self.lease.holder_id,
                    fencing_token=self.lease.fencing_token,
                    expected_attempt=attempt if isinstance(attempt, int) else 0,
                    status=CommandStatus.FAILED,
                    error_code=type(error).__name__,
                    error_detail=redact(str(error)),
                )
            except Exception:
                pass
        finally:
            self._finish_scan_thread()

    def _emit_command(self, command: dict[str, object]) -> None:
        try:
            self.outbox.append_own(
                event_type="command.updated",
                payload={
                    "command_id": command.get("command_id"),
                    "command_type": command.get("command_type"),
                    "status": command.get("status"),
                    "coalesced": bool(command.get("coalesced", False)),
                    "error_code": command.get("error_code"),
                    "requested_by": command.get("requested_by"),
                    "attempts": command.get("attempts"),
                },
                source_kind="command",
                source_id=str(command.get("command_id") or ""),
            )
        except Exception:
            pass

    def _maintenance(self) -> None:
        from datetime import datetime as _dt

        from stock_watcher.domain import SHANGHAI

        now = _dt.now(SHANGHAI)
        try:
            self.outbox.prune(now=now)
            self.secrets.expire_requests(now=now)
            self.secrets.prune_requests(now=now)
        except Exception as error:
            logger.warning("maintenance error: %s", redact(str(error)))


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = ServerSettings.from_env()
    return WorkerRuntime(settings).run()


if __name__ == "__main__":
    raise SystemExit(main())
