"""Container healthchecks must remain read-only against the shared SQLite DB."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stock_watcher.domain import SHANGHAI
from stock_watcher.server import healthcheck
from stock_watcher.server.config import ServerSettings
from stock_watcher.server.web import create_app
from stock_watcher.storage import SQLiteStore


def _settings(tmp_path: Path) -> ServerSettings:
    db = tmp_path / "db" / "stockwatcher.db"
    db.parent.mkdir(parents=True)
    SQLiteStore(db).initialize()
    return ServerSettings(
        environment="test",
        db_path=db,
        report_dir=tmp_path / "reports",
        public_origin="http://testserver",
        source_commit="a" * 40,
    )


def _open_fd_count() -> int:
    proc = Path(f"/proc/{os.getpid()}/fd")
    if proc.is_dir():
        return len(os.listdir(proc))
    try:
        return len(os.listdir("/dev/fd"))
    except OSError:
        return 0


def test_web_healthcheck_opens_read_only_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    observed: list[bool] = []
    original_init = SQLiteStore.__init__

    def spy(self: SQLiteStore, path: Path, read_only: bool = False) -> None:
        observed.append(read_only)
        original_init(self, path, read_only=read_only)

    monkeypatch.setattr(SQLiteStore, "__init__", spy)
    assert healthcheck.check_web(settings) == 0
    assert observed == [True]


def test_worker_healthcheck_opens_read_only_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    observed: list[bool] = []
    original_init = SQLiteStore.__init__

    def spy(self: SQLiteStore, path: Path, read_only: bool = False) -> None:
        observed.append(read_only)
        original_init(self, path, read_only=read_only)

    monkeypatch.setattr(SQLiteStore, "__init__", spy)
    assert healthcheck.check_worker(settings) == 1
    assert observed == [True]


def test_web_readiness_rejects_expired_worker_lease(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)
    now = datetime.now(SHANGHAI)
    with app.state.store.transaction() as connection:
        connection.execute(
            "INSERT INTO service_leases "
            "(lease_name, holder_id, source_commit, acquired_at, heartbeat_at, "
            "expires_at, fencing_token) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "stockwatcher-worker",
                "expired-holder",
                "a" * 40,
                (now - timedelta(minutes=2)).isoformat(),
                (now - timedelta(minutes=2)).isoformat(),
                (now - timedelta(minutes=1)).isoformat(),
                1,
            ),
        )
    response = TestClient(app).get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_web_readiness_uses_fresh_read_only_store(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    app = create_app(settings)

    assert app.state.read_store.read_only is True
    assert app.state.public_state.store is app.state.read_store
    assert app.state.outbox.read_store is app.state.read_store
    assert app.state.commands.read_store is app.state.read_store


def _seed_live_worker(store: SQLiteStore, now: datetime) -> str:
    session_id = "worker-session"
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO service_leases "
            "(lease_name, holder_id, source_commit, acquired_at, heartbeat_at, "
            "expires_at, fencing_token) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "stockwatcher-worker",
                "holder",
                "a" * 40,
                now.isoformat(),
                now.isoformat(),
                (now + timedelta(seconds=20)).isoformat(),
                1,
            ),
        )
    store.start_runtime_session(
        session_id=session_id,
        pid=1,
        ppid=0,
        app_path="test-worker",
        source_commit="a" * 40,
        started_at=now.isoformat(),
    )
    store.record_runtime_event(
        session_id=session_id,
        occurred_at=now.isoformat(),
        event_type="worker.loop",
        detail={},
    )
    return session_id


def _ready_client(tmp_path: Path) -> tuple[TestClient, ServerSettings, SQLiteStore, str]:
    settings = _settings(tmp_path)
    app = create_app(settings)
    now = datetime.now(SHANGHAI)
    session_id = _seed_live_worker(app.state.store, now)
    return TestClient(app), settings, app.state.store, session_id


def test_worker_readiness_rejects_stalled_scan_even_when_lease_is_fresh(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    store = SQLiteStore(settings.db_path)
    now = datetime.now(SHANGHAI)
    session_id = _seed_live_worker(store, now)
    stalled_at = now - timedelta(seconds=settings.worker_scan_timeout_seconds + 1)
    store.record_runtime_event(
        session_id=session_id,
        occurred_at=stalled_at.isoformat(),
        event_type="worker.scan_started",
        detail={"kind": "automatic"},
    )
    ready, status = healthcheck.worker_readiness(store, settings)
    assert not ready
    assert status["worker_lease_held"] is True
    assert status["reason"] == "Worker scan stalled"


def test_health_live_stays_ok_without_worker(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    response = TestClient(create_app(settings)).get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_http_ready_200_when_schema_and_worker_are_fresh(tmp_path: Path) -> None:
    client, _, _, _ = _ready_client(tmp_path)
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_http_ready_uses_asyncio_to_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _, _ = _ready_client(tmp_path)
    seen: list[object] = []
    original = __import__("asyncio").to_thread

    async def spy(fn: object, *args: object, **kwargs: object) -> object:
        seen.append(fn)
        return await original(fn, *args, **kwargs)

    monkeypatch.setattr("asyncio.to_thread", spy)
    response = client.get("/health/ready")
    assert response.status_code == 200
    from stock_watcher.server.web import _readiness_status

    assert _readiness_status in seen


def test_http_ready_503_when_schema_mismatches(tmp_path: Path) -> None:
    client, _, store, _session_id = _ready_client(tmp_path)
    with store.transaction() as connection:
        connection.execute("DELETE FROM schema_version")
        connection.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (9, datetime.now(SHANGHAI).isoformat()),
        )
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_http_ready_503_when_runtime_heartbeat_stale(tmp_path: Path) -> None:
    client, settings, store, session_id = _ready_client(tmp_path)
    stale = datetime.now(SHANGHAI) - timedelta(
        seconds=settings.worker_loop_stale_seconds + 20
    )
    with store.transaction() as connection:
        connection.execute(
            "UPDATE runtime_sessions SET last_heartbeat_at = ? WHERE session_id = ?",
            (stale.isoformat(), session_id),
        )
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_http_ready_503_when_worker_loop_stale(tmp_path: Path) -> None:
    client, settings, store, session_id = _ready_client(tmp_path)
    stale = datetime.now(SHANGHAI) - timedelta(
        seconds=settings.worker_loop_stale_seconds + 20
    )
    store.record_runtime_event(
        session_id=session_id,
        occurred_at=stale.isoformat(),
        event_type="worker.loop",
        detail={},
    )
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_http_ready_503_when_scan_stalled(tmp_path: Path) -> None:
    client, settings, store, session_id = _ready_client(tmp_path)
    stalled_at = datetime.now(SHANGHAI) - timedelta(
        seconds=settings.worker_scan_timeout_seconds + 1
    )
    store.record_runtime_event(
        session_id=session_id,
        occurred_at=stalled_at.isoformat(),
        event_type="worker.scan_started",
        detail={"kind": "automatic"},
    )
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_http_ready_200_during_in_progress_scan(tmp_path: Path) -> None:
    client, _, store, session_id = _ready_client(tmp_path)
    started = datetime.now(SHANGHAI) - timedelta(seconds=5)
    store.record_runtime_event(
        session_id=session_id,
        occurred_at=started.isoformat(),
        event_type="worker.scan_started",
        detail={"kind": "automatic"},
    )
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_http_ready_sqlite_error_stays_minimal_and_logs_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, settings, _, _ = _ready_client(tmp_path)
    secret = "super-secret-token-value"

    def explode(self: SQLiteStore) -> sqlite3.Connection:
        raise sqlite3.OperationalError(
            f"unable to open database file: {settings.db_path} token={secret}"
        )

    monkeypatch.setattr(SQLiteStore, "connect", explode)
    with caplog.at_level("ERROR", logger="stock_watcher.server"):
        response = client.get(
            "/health/ready",
            headers={
                "x-request-id": "corr-ready-1",
                "cookie": "sw_session=secret-cookie-value",
                "authorization": "Bearer secret-header-token",
            },
        )
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "event=web_readiness_failed" in text
    assert "OperationalError" in text
    assert "failure_stage=" in text
    assert "corr-ready-1" in text
    assert str(settings.db_path) not in text
    assert secret not in text
    assert "secret-cookie-value" not in text
    assert "secret-header-token" not in text
    assert "Bearer" not in text


def test_http_ready_repeated_calls_do_not_leak_fds(tmp_path: Path) -> None:
    client, _, _, _ = _ready_client(tmp_path)
    for _ in range(5):
        assert client.get("/health/ready").status_code == 200
    baseline = _open_fd_count()
    for _ in range(40):
        assert client.get("/health/ready").status_code == 200
    assert _open_fd_count() - baseline <= 5


def test_worker_cli_ready_when_seeded(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = SQLiteStore(settings.db_path)
    _seed_live_worker(store, datetime.now(SHANGHAI))
    assert healthcheck.check_worker(settings) == 0
    assert healthcheck.check_web(settings) == 0
