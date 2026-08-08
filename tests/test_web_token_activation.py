"""Web Token activation must match the desktop App's lightweight gate."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import stock_watcher.services.stockwatcher_service as service_module
from stock_watcher.domain import SHANGHAI
from stock_watcher.providers.tushare.errors import (
    ProviderError,
    ProviderFailureReason,
)
from stock_watcher.providers.tushare.transport_protocol import TransportRequest
from stock_watcher.services import CommandService, CommandType, SecretService
from stock_watcher.services.stockwatcher_service import StockWatcherService
from stock_watcher.storage import SQLiteStore


class RecordingPro:
    instances: list[RecordingPro] = []
    failure: ProviderError | None = None

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.calls: list[TransportRequest] = []
        self.__class__.instances.append(self)

    def execute(self, request: TransportRequest) -> SimpleNamespace:
        self.calls.append(request)
        if self.failure is not None:
            raise self.failure
        return SimpleNamespace(records=({"cal_date": "20260807"},))


def make_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "web.sqlite3")
    store.initialize()
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO web_users (user_id, username, password_hash, role, active, "
            "created_at, updated_at, password_changed_at) VALUES (?, ?, ?, 'admin', 1, ?, ?, ?)",
            (
                1,
                "unit-admin",
                "x" * 97,
                "2026-08-07T00:00:00+08:00",
                "2026-08-07T00:00:00+08:00",
                "2026-08-07T00:00:00+08:00",
            ),
        )
    return store


def fixed_now() -> datetime:
    return datetime(2026, 8, 8, 10, 0, tzinfo=SHANGHAI)


def test_probe_token_calls_only_trade_calendar(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    RecordingPro.instances.clear()
    RecordingPro.failure = None
    monkeypatch.setattr(service_module, "TushareSdkProTransport", RecordingPro)

    service = StockWatcherService(make_store(tmp_path), clock=fixed_now)
    result = service._probe_token("candidate-token-never-persisted")  # noqa: SLF001

    assert result["ok"] is True
    assert result["layers"] == [{"layer": "trade_calendar", "ok": True, "rows": 1}]
    assert result["realtime_route"] == "native_realtime"
    assert len(RecordingPro.instances) == 1
    request = RecordingPro.instances[0].calls[0]
    assert request.api_name == "trade_cal"
    assert request.allow_empty is True
    assert "stock_basic" not in json.dumps(result)


def test_probe_token_returns_non_sensitive_provider_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    RecordingPro.instances.clear()
    RecordingPro.failure = ProviderError(
        ProviderFailureReason.PERMISSION_DENIED,
        http_status=403,
    )
    monkeypatch.setattr(service_module, "TushareSdkProTransport", RecordingPro)

    service = StockWatcherService(make_store(tmp_path), clock=fixed_now)
    result = service._probe_token("candidate-token-never-persisted")  # noqa: SLF001

    assert result["ok"] is False
    assert result["error_code"] == "trade_calendar:permission_denied"
    assert result["diagnostic"] == {"reason": "permission_denied", "http_status": 403}
    assert "candidate-token-never-persisted" not in json.dumps(result)


def test_token_update_activates_after_lightweight_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    RecordingPro.instances.clear()
    RecordingPro.failure = None
    monkeypatch.setattr(service_module, "TushareSdkProTransport", RecordingPro)
    store = make_store(tmp_path)
    secrets = SecretService(store, master_key=os.urandom(32))
    commands = CommandService(store, clock=fixed_now)
    candidate = "candidate-token-never-persisted"
    request = secrets.create_request(
        candidate_token=candidate,
        purpose="token_update",
        requested_by=1,
    )
    created = commands.create(
        command_type=CommandType.TOKEN_UPDATE,
        requested_by=1,
        secret_request_id=request["request_id"],
    )
    claimed = commands.claim_next(holder_id="worker", fencing_token=1, now=fixed_now())
    assert claimed is not None

    service = StockWatcherService(
        store,
        commands=commands,
        secrets=secrets,
        clock=fixed_now,
    )
    service._holder_id = "worker"  # noqa: SLF001
    service._fencing_token = 1  # noqa: SLF001
    service.handle_command(claimed)

    completed = commands.get(created["command_id"])
    assert completed is not None
    assert completed["status"] == "succeeded"
    assert secrets.active_token() == candidate
    assert [call.api_name for call in RecordingPro.instances[0].calls] == ["trade_cal"]
