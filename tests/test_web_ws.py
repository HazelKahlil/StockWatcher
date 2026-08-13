"""WebSocket reconnect/resync tests against the event outbox."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient as FastAPITestClient
from starlette.websockets import WebSocketDisconnect

from stock_watcher.server.auth import RateLimiter
from stock_watcher.server.config import ServerSettings
from stock_watcher.server.web import create_app
from stock_watcher.storage import SQLiteStore


class TestClient(FastAPITestClient):
    def __init__(self, app: Any, **kwargs: Any) -> None:
        headers = dict(kwargs.pop("headers", {}))
        headers.setdefault("Origin", str(app.state.settings.public_origin))
        super().__init__(app, headers=headers, **kwargs)


@pytest.fixture()
def app_env(tmp_path: Path) -> tuple[Any, SQLiteStore]:
    import base64
    import os

    master_key_file = tmp_path / "master.key"
    master_key_file.write_text(base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"))
    settings = ServerSettings(
        environment="test",
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


def test_ws_fresh_page_without_cursor_starts_at_current_watermark(
    app_env: tuple[Any, SQLiteStore],
) -> None:
    app, store = app_env
    latest = seed_events(app, store, 5)
    client = login(app)
    with client.websocket_connect("/ws/v1/events") as websocket:
        hello = json.loads(websocket.receive_text())
        snapshot = json.loads(websocket.receive_text())
        assert hello["event_type"] == "server.hello"
        assert hello["payload"]["latest_event_id"] == latest
        assert snapshot["event_type"] == "state.snapshot"

        new_event_id = app.state.outbox.append_own(
            event_type="alert.created",
            payload={"alert_id": 6, "trigger_type": "intraday"},
            source_kind="alert",
            source_id="6",
        )
        event = json.loads(websocket.receive_text())
        assert event["event_type"] == "alert.created"
        assert event["event_id"] == new_event_id == latest + 1


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


def test_ws_resync_required_when_cursor_is_ahead_after_restore(
    app_env: tuple[Any, SQLiteStore],
) -> None:
    app, store = app_env
    latest = seed_events(app, store, 3)
    client = login(app)
    with client.websocket_connect("/ws/v1/events?after_id=99") as websocket:
        hello = json.loads(websocket.receive_text())
        resync = json.loads(websocket.receive_text())
        assert hello["event_type"] == "server.hello"
        assert resync["event_type"] == "server.resync_required"
        assert resync["payload"]["reason"] == "cursor_ahead"
        assert resync["payload"]["latest_event_id"] == latest == 3


@pytest.mark.parametrize("cursor", ["not-a-number", "-1"])
def test_ws_resync_required_on_invalid_cursor(
    app_env: tuple[Any, SQLiteStore], cursor: str
) -> None:
    app, store = app_env
    latest = seed_events(app, store, 2)
    client = login(app)
    with client.websocket_connect(f"/ws/v1/events?after_id={cursor}") as websocket:
        hello = json.loads(websocket.receive_text())
        resync = json.loads(websocket.receive_text())
        assert hello["event_type"] == "server.hello"
        assert resync["event_type"] == "server.resync_required"
        assert resync["payload"]["reason"] == "cursor_invalid"
        assert resync["payload"]["latest_event_id"] == latest == 2


def test_ws_hides_admin_events_and_advances_with_safe_cursor(
    app_env: tuple[Any, SQLiteStore],
) -> None:
    app, _ = app_env
    app.state.outbox.append_own(
        event_type="admin.diagnostic",
        payload={"private": True},
        source_kind="admin",
        source_id="1",
        visibility="admin",
    )
    client = login(app)
    with client.websocket_connect("/ws/v1/events?after_id=0") as websocket:
        assert json.loads(websocket.receive_text())["event_type"] == "server.hello"
        assert json.loads(websocket.receive_text())["event_type"] == "state.snapshot"
        cursor = json.loads(websocket.receive_text())
        assert cursor["event_type"] == "server.cursor"
        assert cursor["event_id"] == 1
        assert cursor["payload"] == {"last_event_id": 1}


def test_ws_command_updates_are_visible_only_to_requester(
    app_env: tuple[Any, SQLiteStore],
) -> None:
    app, _ = app_env
    app.state.outbox.append_own(
        event_type="command.updated",
        payload={"command_id": "private", "status": "running", "requested_by": 999},
        source_kind="command",
        source_id="private",
    )
    client = login(app)
    with client.websocket_connect("/ws/v1/events?after_id=0") as websocket:
        websocket.receive_text()
        websocket.receive_text()
        cursor = json.loads(websocket.receive_text())
        assert cursor["event_type"] == "server.cursor"
        assert cursor["event_id"] == 1


def test_ws_unauthorized_rejected(app_env: tuple[Any, SQLiteStore]) -> None:
    app, _ = app_env
    anon = TestClient(app)
    with pytest.raises(Exception):
        with anon.websocket_connect("/ws/v1/events?after_id=0") as websocket:
            websocket.receive_text()


def test_ws_closes_after_logout(app_env: tuple[Any, SQLiteStore]) -> None:
    app, _ = app_env
    client = login(app)
    csrf = client.get("/api/v1/me").json()["csrf_token"]
    with client.websocket_connect("/ws/v1/events") as websocket:
        websocket.receive_text()
        websocket.receive_text()
        response = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 204
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_text()
        assert closed.value.code == 4401


@pytest.mark.parametrize(
    "mutation",
    [
        {"password": "replacement-password-123"},
        {"active": False},
    ],
    ids=["password-change", "deactivation"],
)
def test_ws_closes_after_account_security_change(
    app_env: tuple[Any, SQLiteStore],
    mutation: dict[str, object],
) -> None:
    app, _ = app_env
    tester = app.state.auth.users.get_by_username("tester1")
    assert tester is not None
    app.state.auth.create_user(
        username="admin1",
        password="admin-password-123",
        role="admin",
    )
    tester_client = login(app)
    admin_client = login(app, "admin1", "admin-password-123")
    csrf = admin_client.get("/api/v1/me").json()["csrf_token"]
    with tester_client.websocket_connect("/ws/v1/events") as websocket:
        websocket.receive_text()
        websocket.receive_text()
        changed = admin_client.patch(
            f"/api/v1/admin/users/{tester['user_id']}",
            json=mutation,
            headers={"X-CSRF-Token": csrf},
        )
        assert changed.status_code == 200
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_text()
        assert closed.value.code == 4401


def test_ws_admin_visibility_removed_after_role_change(
    app_env: tuple[Any, SQLiteStore],
) -> None:
    app, _ = app_env
    tester = app.state.auth.users.get_by_username("tester1")
    assert tester is not None
    app.state.auth.create_user(
        username="admin1",
        password="admin-password-123",
        role="admin",
    )
    tester_client = login(app)
    admin_client = login(app, "admin1", "admin-password-123")
    csrf = admin_client.get("/api/v1/me").json()["csrf_token"]
    with tester_client.websocket_connect("/ws/v1/events") as websocket:
        websocket.receive_text()
        websocket.receive_text()
        changed = admin_client.patch(
            f"/api/v1/admin/users/{tester['user_id']}",
            json={"role": "admin"},
            headers={"X-CSRF-Token": csrf},
        )
        assert changed.status_code == 200
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_text()
        assert closed.value.code == 4403


def test_ws_closes_after_absolute_expiry(app_env: tuple[Any, SQLiteStore]) -> None:
    app, store = app_env
    app.state.ws_manager.auth_check_seconds = 0.0
    client = login(app)
    with client.websocket_connect("/ws/v1/events") as websocket:
        websocket.receive_text()
        websocket.receive_text()
        with store.transaction() as connection:
            connection.execute(
                "UPDATE web_sessions SET absolute_expires_at = ?",
                ("2000-01-01T00:00:00+08:00",),
            )
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_text()
        assert closed.value.code == 4401


@pytest.mark.parametrize("origin", [None, "https://testserver", "http://evil.example"])
def test_ws_rejects_missing_or_mismatched_origin(
    app_env: tuple[Any, SQLiteStore],
    origin: str | None,
) -> None:
    app, _ = app_env
    client = FastAPITestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "tester1", "password": "tester-pass-123"},
        headers={"Origin": app.state.settings.public_origin},
    )
    assert response.status_code == 200
    headers = {} if origin is None else {"Origin": origin}
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/ws/v1/events", headers=headers) as websocket:
            websocket.receive_text()
    assert closed.value.code == 4403


def test_ws_enforces_per_user_connection_cap(app_env: tuple[Any, SQLiteStore]) -> None:
    app, _ = app_env
    app.state.ws_manager.max_per_user = 1
    client = login(app)
    with client.websocket_connect("/ws/v1/events") as first:
        first.receive_text()
        first.receive_text()
        with client.websocket_connect("/ws/v1/events") as second:
            with pytest.raises(WebSocketDisconnect) as closed:
                second.receive_text()
            assert closed.value.code == 4429


def test_ws_enforces_global_connection_rate(app_env: tuple[Any, SQLiteStore]) -> None:
    app, _ = app_env
    app.state.auth.websocket_global_limiter = RateLimiter(
        max_attempts=1,
        window_seconds=60,
        max_keys=1,
    )
    client = login(app)
    with client.websocket_connect("/ws/v1/events") as first:
        first.receive_text()
        first.receive_text()
    with client.websocket_connect("/ws/v1/events") as second:
        with pytest.raises(WebSocketDisconnect) as closed:
            second.receive_text()
        assert closed.value.code == 4429


def test_ws_user_rate_limit_does_not_block_other_users(
    app_env: tuple[Any, SQLiteStore],
) -> None:
    app, _ = app_env
    app.state.auth.create_user(
        username="tester2",
        password="tester-two-pass-123",
        role="tester",
    )
    app.state.auth.websocket_user_limiter = RateLimiter(
        max_attempts=1,
        window_seconds=60,
        max_keys=10,
    )
    app.state.auth.websocket_ip_limiter = RateLimiter(
        max_attempts=10,
        window_seconds=60,
        max_keys=10,
    )
    app.state.auth.websocket_global_limiter = RateLimiter(
        max_attempts=10,
        window_seconds=60,
        max_keys=1,
    )
    first_user = login(app)
    second_user = login(app, "tester2", "tester-two-pass-123")
    with first_user.websocket_connect("/ws/v1/events") as first:
        first.receive_text()
        first.receive_text()
    with first_user.websocket_connect("/ws/v1/events") as blocked:
        with pytest.raises(WebSocketDisconnect) as closed:
            blocked.receive_text()
        assert closed.value.code == 4429
    with second_user.websocket_connect("/ws/v1/events") as allowed:
        assert json.loads(allowed.receive_text())["event_type"] == "server.hello"
        assert json.loads(allowed.receive_text())["event_type"] == "state.snapshot"


def test_ws_rejected_active_cap_does_not_consume_global_budget(
    app_env: tuple[Any, SQLiteStore],
) -> None:
    app, _ = app_env
    app.state.ws_manager.max_per_user = 1
    app.state.auth.websocket_global_limiter = RateLimiter(
        max_attempts=2,
        window_seconds=60,
        max_keys=1,
    )
    client = login(app)
    with client.websocket_connect("/ws/v1/events") as first:
        first.receive_text()
        first.receive_text()
        with client.websocket_connect("/ws/v1/events") as rejected:
            with pytest.raises(WebSocketDisconnect) as closed:
                rejected.receive_text()
            assert closed.value.code == 4429
    with client.websocket_connect("/ws/v1/events") as second_valid:
        assert json.loads(second_valid.receive_text())["event_type"] == "server.hello"
        assert json.loads(second_valid.receive_text())["event_type"] == "state.snapshot"


def test_ws_global_rate_is_above_normal_reconnect_burst() -> None:
    settings = ServerSettings(environment="test", public_origin="http://testserver")
    settings.validate_for_web()
    assert settings.rate_limits.websocket_global_connect_max >= settings.websocket_max_global
    assert (
        settings.rate_limits.websocket_global_connect_max
        > settings.rate_limits.websocket_ip_connect_max
        > settings.rate_limits.websocket_user_connect_max
    )


def test_browser_fresh_load_uses_server_watermark_then_reconnects_incrementally() -> None:
    script = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "stock_watcher"
        / "server"
        / "static"
        / "app.js"
    ).read_text(encoding="utf-8")
    assert "let lastEventId = null;" in script
    assert "lastEventId == null ? ''" in script
    assert "event.event_type === 'server.hello' && lastEventId == null" in script
    assert "lastEventId = Number(event.payload?.latest_event_id || 0);" in script
    assert "event.event_type === 'server.resync_required'" in script
    assert "if (csrfToken)" not in script
    api_wrapper = script.split("export async function apiJson", 1)[0]
    assert api_wrapper.count("await response.json()") == 1
    assert "error.status = response.status" in api_wrapper
    assert "error.code =" in api_wrapper
    assert "error.payload = payload" in api_wrapper
    assert "event.code === 4401 || event.code === 4403" in script
    assert "1000 * (2 ** reconnectAttempt)" in script
    assert "Math.random() * 500" in script
    assert "setTimeout(connectEvents, 3000)" not in script
