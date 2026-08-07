"""WebSocket reconnect/resync tests against the event outbox."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from stock_watcher.server.config import ServerSettings
from stock_watcher.server.web import create_app
from stock_watcher.storage import SQLiteStore


@pytest.fixture()
def app_env(tmp_path: Path) -> tuple[Any, SQLiteStore]:
    import base64
    import os

    master_key_file = tmp_path / "master.key"
    master_key_file.write_text(base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"))
    settings = ServerSettings(
        db_path=tmp_path / "db" / "test.db",
        report_dir=tmp_path / "reports",
        master_key_file=master_key_file,
        public_origin="http://testserver",
    )
    app = create_app(settings)
    store: SQLiteStore = app.state.store
    auth = app.state.auth
    auth.create_user(username="tester1", password="tester-pass-123", role="tester")
    return app, store


def login(app: Any, username: str = "tester1", password: str = "tester-pass-123") -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return client


def seed_events(app: Any, store: SQLiteStore, count: int = 5) -> int:
    outbox = app.state.outbox
    latest = 0
    for index in range(1, count + 1):
        with store.transaction() as connection:
            latest = outbox.append(
                connection,
                event_type="candidates.updated",
                payload={"snapshot_id": index, "state_version": index},
                source_kind="snapshot",
                source_id=str(index),
            )
    return latest


def test_ws_hello_snapshot_and_replay(app_env: tuple[Any, SQLiteStore]) -> None:
    app, store = app_env
    latest = seed_events(app, store, 5)
    client = login(app)
    with client.websocket_connect("/ws/v1/events?after_id=0") as websocket:
        hello = json.loads(websocket.receive_text())
        assert hello["event_type"] == "server.hello"
        assert hello["payload"]["business_timezone"] == "Asia/Shanghai"
        assert hello["payload"]["latest_event_id"] == latest
        snapshot = json.loads(websocket.receive_text())
        assert snapshot["event_type"] == "state.snapshot"
        # Missed events replay in order.
        events: list[dict[str, object]] = []
        while len(events) < 5:
            message = json.loads(websocket.receive_text())
            if message["event_type"] == "candidates.updated":
                events.append(message)
        assert [e["event_id"] for e in events] == [1, 2, 3, 4, 5]


def test_ws_reconnect_with_after_id(app_env: tuple[Any, SQLiteStore]) -> None:
    app, store = app_env
    seed_events(app, store, 3)
    client = login(app)
    with client.websocket_connect("/ws/v1/events?after_id=2") as websocket:
        messages = [json.loads(websocket.receive_text()) for _ in range(2)]
        assert messages[0]["event_type"] == "server.hello"
        assert messages[1]["event_type"] == "state.snapshot"
        # The only missed event (id 3) replays next.
        replay = json.loads(websocket.receive_text())
        assert replay["event_type"] == "candidates.updated"
        assert replay["event_id"] == 3
        assert replay["payload"]["snapshot_id"] == 3


def test_ws_resync_required_on_expired_cursor(app_env: tuple[Any, SQLiteStore]) -> None:
    app, store = app_env
    seed_events(app, store, 5)
    # Drop the two oldest events so the retention window starts at id 3.
    with store.transaction() as connection:
        connection.execute("DELETE FROM web_events WHERE event_id <= 2")
    client = login(app)
    # after_id=1 predates the retained window -> resync_required.
    with client.websocket_connect("/ws/v1/events?after_id=1") as websocket:
        messages = [json.loads(websocket.receive_text()) for _ in range(2)]
        assert messages[0]["event_type"] == "server.hello"
        assert messages[1]["event_type"] == "server.resync_required"
        assert messages[1]["payload"]["reason"] == "cursor_expired"
        assert messages[1]["payload"]["minimum_event_id"] == 3


def test_ws_unauthorized_rejected(app_env: tuple[Any, SQLiteStore]) -> None:
    app, _ = app_env
    anon = TestClient(app)
    with pytest.raises(Exception):
        with anon.websocket_connect("/ws/v1/events?after_id=0") as websocket:
            websocket.receive_text()
