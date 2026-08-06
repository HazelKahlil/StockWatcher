from __future__ import annotations

import faulthandler
import json
import os
import platform
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from .build_info import source_commit
from .paths import RuntimePaths, runtime_paths

_SENSITIVE_VALUE = re.compile(
    r"(?i)(token|secret|password|authorization|bearer|api[-_]?key)(\s*[=:]\s*|\s+)([^\s,;]+)"
)


class StartupRecorder:
    """Write a small, credential-free audit trail for every application start."""

    def __init__(self, paths: RuntimePaths | None = None) -> None:
        self.paths = paths or runtime_paths()
        self.paths.create()
        self.runtime_dir = self.paths.root / "runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.paths.logs / f"startup-{datetime.now().strftime('%Y%m%d')}.log"
        self.state_path = self.runtime_dir / "last-startup.json"
        self.error_path = self.runtime_dir / "last-startup-error.txt"
        self.fault_path = self.runtime_dir / "last-startup-faulthandler.log"
        self._fault_file: Any | None = None
        self._failure_recorded = False
        self._previous_excepthook = sys.excepthook
        self._state: dict[str, Any] = {
            "schema_version": 1,
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "app_path": _application_path(),
            "source_commit": source_commit(),
            "argv": _safe_argv(sys.argv),
            "platform": sys.platform,
            "os_version": platform.mac_ver()[0] or platform.platform(),
            "architecture": platform.machine(),
            "started_at": _now(),
            "stage": "boot",
            "events": [],
            "exit_code": None,
            "exit_reason": None,
        }
        self._write_state()
        self.stage("boot")
        self._enable_fault_handler()
        sys.excepthook = self._handle_uncaught_exception

    @property
    def data(self) -> dict[str, Any]:
        return dict(self._state)

    def stage(self, name: str, **fields: object) -> None:
        event: dict[str, Any] = {"at": _now(), "stage": name}
        event.update({key: _safe_value(value) for key, value in fields.items()})
        self._state["stage"] = name
        for key, value in event.items():
            if key not in {"at", "stage"}:
                self._state[key] = value
        events = self._state.setdefault("events", [])
        if isinstance(events, list):
            events.append(event)
        self._write_log(event)
        self._write_state()

    def set_paths(self, paths: RuntimePaths) -> None:
        self.stage(
            "paths-created",
            data_dir=str(paths.data),
            database_path=str(paths.database),
            logs_dir=str(paths.logs),
            runtime_dir=str(self.runtime_dir),
        )

    def record_error(
        self,
        error: BaseException,
        *,
        app_available: bool,
        stage: str,
    ) -> None:
        """Persist an exception without turning normal process exits into fatals."""
        if self._failure_recorded:
            return
        self._failure_recorded = True
        rendered = _safe_text("".join(traceback.format_exception(error)))
        try:
            self.error_path.write_text(rendered, encoding="utf-8")
        except OSError:
            pass
        self.stage(
            stage,
            error_type=type(error).__name__,
            error_message=_safe_text(str(error)),
            error_path=str(self.error_path),
        )
        _show_fatal_message(self.error_path, app_available=app_available)

    def fatal(self, error: BaseException, *, app_available: bool) -> None:
        self.record_error(
            error,
            app_available=app_available,
            stage="startup_fatal_error",
        )

    def finish(self, exit_code: int, reason: str) -> None:
        self._state["exit_code"] = int(exit_code)
        self._state["exit_reason"] = _safe_text(reason)
        self._state["ended_at"] = _now()
        if not self._failure_recorded:
            self.stage("graceful_exit", exit_code=int(exit_code), exit_reason=reason)
        else:
            # Keep the failure stage as the terminal semantic event.  A later
            # bookkeeping call must not make a fatal startup look graceful.
            self._write_state()
        sys.excepthook = self._previous_excepthook
        if self._fault_file is not None:
            try:
                self._fault_file.flush()
                self._fault_file.close()
            except OSError:
                pass
            self._fault_file = None

    def _handle_uncaught_exception(
        self,
        error_type: type[BaseException],
        error: BaseException,
        traceback_object: Any,
    ) -> None:
        del error_type, traceback_object
        self.fatal(error, app_available=False)
        self._previous_excepthook(type(error), error, error.__traceback__)

    def _enable_fault_handler(self) -> None:
        try:
            self._fault_file = self.fault_path.open("a", encoding="utf-8")
            faulthandler.enable(file=self._fault_file, all_threads=True)
        except (OSError, RuntimeError):
            self._fault_file = None

    def _write_log(self, event: dict[str, Any]) -> None:
        try:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        except OSError:
            pass

    def _write_state(self) -> None:
        temporary = self.state_path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.state_path)
        except OSError:
            temporary.unlink(missing_ok=True)


def _safe_argv(argv: list[str]) -> list[str]:
    safe: list[str] = []
    redact_next = False
    for value in argv:
        if redact_next:
            safe.append("[REDACTED]")
            redact_next = False
            continue
        lowered = value.casefold()
        if any(
            marker in lowered
            for marker in ("token", "secret", "password", "authorization", "bearer")
        ):
            if "=" not in value:
                redact_next = True
            safe.append(
                _safe_text(value.split("=", 1)[0] + "=[REDACTED]")
                if "=" in value
                else value
            )
        else:
            safe.append(_safe_text(value))
    return safe


def _safe_text(value: str) -> str:
    return _SENSITIVE_VALUE.sub(r"\1=[REDACTED]", value)


def _safe_value(value: object) -> object:
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in value.items()}
    return _safe_text(str(value))


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _application_path() -> str:
    executable = Path(sys.executable).resolve()
    for parent in (executable, *executable.parents):
        if parent.suffix == ".app":
            return str(parent)
    return str(Path(sys.argv[0]).resolve())


def _show_fatal_message(error_path: Path, *, app_available: bool) -> None:
    message = f"StockWatcher 启动失败。详细日志：{error_path}"
    if app_available:
        try:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(None, "StockWatcher 启动失败", message)
            return
        except Exception:
            pass
    if sys.platform == "darwin":
        try:
            subprocess.Popen(
                [
                    "/usr/bin/osascript",
                    "-e",
                    "on run argv\n"
                    "display dialog item 1 of argv with title \"StockWatcher 启动失败\" "
                    "buttons {\"好\"}\n"
                    "end run",
                    message,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass


def new_startup_recorder() -> StartupRecorder:
    return StartupRecorder()
