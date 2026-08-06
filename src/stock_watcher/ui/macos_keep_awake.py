"""Trading-hours keep-awake and user-level 09:20 auto-start (macOS only).

Internal use: prevents idle system sleep during trading hours without touching
global power settings, and installs a per-user LaunchAgent for a weekday
09:20 launch attempt.  The App itself decides whether the day is a trading day
and never creates a second instance.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from datetime import datetime, time
from pathlib import Path
from typing import Any

from stock_watcher.domain import SHANGHAI

AUTOSTART_LABEL = "com.kahlilhazel.stockwatcher.autostart"
SETTING_KEEP_AWAKE = "keep_awake_during_trading_hours"
SETTING_AUTOSTART = "auto_start_09_20"

TRADING_START = time(9, 20)
TRADING_END = time(15, 40)


class TradingHoursKeepAwake:
    """Runs a scoped ``caffeinate -i`` child that dies with the App process."""

    def __init__(self, *, clock: Any | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(SHANGHAI))
        self._process: subprocess.Popen[bytes] | None = None

    def update(self, enabled: bool) -> None:
        """Start or stop the assertion to match the setting and the window."""
        now = self._clock()
        inside = TRADING_START <= now.timetz().replace(tzinfo=None) <= TRADING_END
        should_run = enabled and inside
        if should_run and self._process is None:
            self._start()
        elif not should_run and self._process is not None:
            self._stop()

    @property
    def running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def _start(self) -> None:
        try:
            self._process = subprocess.Popen(
                [
                    "/usr/bin/caffeinate",
                    "-i",
                    "-w",
                    str(os.getpid()),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            # caffeinate is part of macOS; absence must degrade to "no
            # assertion" instead of crashing the App.
            self._process = None

    def _stop(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()

    def shutdown(self) -> None:
        self._stop()


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{AUTOSTART_LABEL}.plist"


def install_launch_agent() -> bool:
    """Install the weekday 09:20 LaunchAgent; returns True when installed."""
    path = launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    program = ["/usr/bin/open", "-a", "StockWatcher"]
    weekdays = [1, 2, 3, 4, 5]
    calendar = [
        {
            "Weekday": weekday,
            "Hour": 9,
            "Minute": 20,
        }
        for weekday in weekdays
    ]
    payload = {
        "Label": AUTOSTART_LABEL,
        "ProgramArguments": program,
        "StartCalendarInterval": calendar,
        "RunAtLoad": False,
        "ProcessType": "Background",
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("wb") as handle:
            plistlib.dump(payload, handle)
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        return False
    subprocess.run(
        ["/bin/launchctl", "load", str(path)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return True


def uninstall_launch_agent() -> bool:
    """Unload and remove the LaunchAgent; returns True when it is gone."""
    path = launch_agent_path()
    if path.is_file():
        subprocess.run(
            ["/bin/launchctl", "unload", str(path)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            path.unlink()
        except OSError:
            return False
    return True


def launch_agent_status() -> str:
    path = launch_agent_path()
    if not path.is_file():
        return "not-installed"
    try:
        with path.open("rb") as handle:
            payload = plistlib.load(handle)
        if payload.get("Label") != AUTOSTART_LABEL:
            return "foreign"
        return "installed"
    except plistlib.InvalidFileException:
        return "corrupt"
