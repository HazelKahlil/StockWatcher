from __future__ import annotations

import plistlib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from stock_watcher.ui.macos_keep_awake import (
    AUTOSTART_LABEL,
    TRADING_END,
    TRADING_START,
    TradingHoursKeepAwake,
    install_launch_agent,
    launch_agent_path,
    launch_agent_status,
    uninstall_launch_agent,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _clock_at(hour: int, minute: int) -> object:
    def clock() -> datetime:
        return datetime(2026, 8, 6, hour, minute, tzinfo=SHANGHAI)

    return clock


def _fake_process() -> SimpleNamespace:
    return SimpleNamespace(
        poll=lambda: None,
        terminate=lambda: None,
        wait=lambda *a, **k: None,
        kill=lambda: None,
    )


def test_keep_awake_asserts_during_trading_hours_and_releases_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keep = TradingHoursKeepAwake(
        clock=_clock_at(TRADING_START.hour, TRADING_START.minute)
    )
    process = _fake_process()
    monkeypatch.setattr(
        "stock_watcher.ui.macos_keep_awake.subprocess.Popen",
        lambda *_a, **_k: process,
    )
    keep.update(True)
    assert keep.running
    keep.update(False)
    assert not keep.running
    keep.shutdown()


def test_keep_awake_ignores_nontrading_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    keep = TradingHoursKeepAwake(clock=_clock_at(TRADING_END.hour + 1, 0))
    process = _fake_process()
    monkeypatch.setattr(
        "stock_watcher.ui.macos_keep_awake.subprocess.Popen",
        lambda *_a, **_k: process,
    )
    keep.update(True)
    assert not keep.running
    keep.shutdown()


def test_keep_awake_uses_scoped_caffeinate_command(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def spawn(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return _fake_process()

    monkeypatch.setattr(
        "stock_watcher.ui.macos_keep_awake.subprocess.Popen",
        spawn,
    )
    keep = TradingHoursKeepAwake(clock=_clock_at(10, 0))
    keep.update(True)
    assert commands and commands[0][:3] == ["/usr/bin/caffeinate", "-i", "-w"]
    keep.shutdown()


def test_launch_agent_install_uninstall_roundtrip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(
        "stock_watcher.ui.macos_keep_awake.subprocess.run",
        lambda *_a, **_k: None,
    )
    assert not launch_agent_path().exists()
    assert install_launch_agent()
    path = launch_agent_path()
    assert path.is_file()
    with path.open("rb") as handle:
        payload = plistlib.load(handle)
    assert payload["Label"] == AUTOSTART_LABEL
    assert payload["ProgramArguments"] == ["/usr/bin/open", "-a", "StockWatcher"]
    assert len(payload["StartCalendarInterval"]) == 5
    assert all(
        item["Hour"] == 9 and item["Minute"] == 20
        for item in payload["StartCalendarInterval"]
    )
    assert launch_agent_status() == "installed"
    assert uninstall_launch_agent()
    assert not launch_agent_path().exists()
    assert launch_agent_status() == "not-installed"
