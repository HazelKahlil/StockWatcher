"""Unique background Worker.

Owns the lease, the runtime, scans, automation, commands and the outbox.
A second instance (``docker compose up --scale worker=2``) fails the lease
and exits safely without ever scanning.
"""
from __future__ import annotations

import logging
import signal
import threading
import time as time_module

from stock_watcher.build_info import source_commit
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
        )
        self._stop = threading.Event()
        self._lease_lost = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self._runtime_heartbeat_thread: threading.Thread | None = None
        self._scan_thread: threading.Thread | None = None
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
        last_tick = 0.0
        last_maintenance = 0.0
        while not self._stop.is_set():
            try:
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

    def _tick(self) -> None:
        self.lease.renew()
        # Recover crashed/expired commands first so obligations never vanish.
        transitions = self.commands.expire_stale()
        for transition in transitions:
            self._emit_command(transition)
        tick = self.service.tick()
        if tick.skipped_reason is not None:
            logger.debug("tick skipped: %s", tick.skipped_reason)
        claimed = self.commands.claim_next(
            holder_id=self.lease.holder_id,
            fencing_token=self.lease.fencing_token,
        )
        if claimed is not None:
            command = claimed
            self._emit_command(command)
            if self._scan_thread is None or not self._scan_thread.is_alive():
                self._scan_thread = threading.Thread(
                    target=self._run_command_safe,
                    args=(command,),
                    daemon=True,
                    name="worker-command",
                )
                self._scan_thread.start()
            else:
                # A long-running command is in flight; keep this one queued by
                # restoring it so it is claimed on the next tick.
                self.commands.complete(
                    str(command["command_id"]),
                    holder_id=self.lease.holder_id,
                    fencing_token=self.lease.fencing_token,
                    status=CommandStatus.FAILED,
                    error_code="busy",
                    error_detail="previous command still running",
                )

    def _run_command_safe(self, command: dict[str, object]) -> None:
        try:
            self.service.handle_command(command)
        except Exception as error:
            logger.error("command %s failed: %s", command.get("command_id"), redact(str(error)))
            try:
                self.commands.complete(
                    str(command["command_id"]),
                    holder_id=self.lease.holder_id,
                    fencing_token=self.lease.fencing_token,
                    status=CommandStatus.FAILED,
                    error_code=type(error).__name__,
                    error_detail=redact(str(error)),
                )
            except Exception:
                pass

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
