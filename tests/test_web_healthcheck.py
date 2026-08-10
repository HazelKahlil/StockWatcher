"""Container healthchecks must remain read-only against the shared SQLite DB."""
from __future__ import annotations

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
        db_path=db,
        report_dir=tmp_path / "reports",
        public_origin="http://testserver",
    )


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
    assert response.json()["worker_lease_held"] is False


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
