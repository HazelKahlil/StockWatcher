"""REQUIRED after integration: exercises StockWatcher's real app/auth/CSRF adapter.

These tests are provided but cannot be run against the source-less isolated kit.
Run in the complete target worktree; never skip them to claim integration passed.
"""
from __future__ import annotations

import base64
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from approval_test_support import seed_snapshot
from fastapi.testclient import TestClient

from stock_watcher.feedback.schema import database, install_on_connection
from stock_watcher.server.config import ServerSettings
from stock_watcher.server.web import create_app


def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> Any:
    monkeypatch.setenv("STOCKWATCHER_CANDIDATE_APPROVALS", "1" if enabled else "0")
    key = tmp_path / "test-master.key"
    key.write_text(base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"))
    settings = ServerSettings(
        environment="test", db_path=tmp_path / "db" / "app.db",
        report_dir=tmp_path / "reports", master_key_file=key,
        public_origin="http://testserver",
    )
    app = create_app(settings)
    users = (("approval-a", "tester"), ("approval-b", "tester"), ("approval-admin", "admin"))
    for name, role in users:
        app.state.auth.create_user(username=name, password="isolated-approval-test", role=role)
    with app.state.store.transaction() as connection:
        seed_snapshot(connection)
    return app


def login(client: TestClient, name: str) -> str:
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/login",
        json={"username": name, "password": "isolated-approval-test"},
    )
    assert response.status_code == 200
    return str(client.get("/api/v1/me").json()["csrf_token"])


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = make_app(tmp_path, monkeypatch, enabled=True)
    with database(app.state.settings.db_path, write=True) as connection:
        install_on_connection(connection)
    with TestClient(app, headers={"Origin": "http://testserver"}) as value:
        yield value


def payload(**changes: Any) -> dict[str, Any]:
    return {"snapshot_id": 1, "selected": True, "request_id": uuid.uuid4().hex,
            "expected_version": 0, **changes}


def test_real_app_user_isolation_csrf_and_private_data(client: TestClient) -> None:
    token = login(client, "approval-a")
    before = client.get("/api/v1/state").json()
    result = client.put("/api/v1/me/candidate-approvals/300829.SZ", json=payload(),
                        headers={"X-CSRF-Token": token})
    assert result.status_code == 200
    assert result.json()["state"]["selected"]
    page = client.get("/")
    assert 'data-approvals-enabled="true"' in page.text
    assert '/static/candidate-approvals.css' in page.text
    assert client.get("/api/v1/me/candidate-approvals/events").json()["items"]
    after = client.get("/api/v1/state").json()
    assert before["candidates"] == after["candidates"]
    assert before["snapshot_id"] == after["snapshot_id"]
    assert "approval" not in str(after).lower()
    assert client.get("/api/v1/admin/candidate-approvals/events").status_code == 403
    login(client, "approval-b")
    state = client.get("/api/v1/me/candidate-approvals/state?snapshot_id=1").json()
    assert not state["items"][0]["selected"]
    assert client.get("/api/v1/me/candidate-approvals/events").json()["items"] == []
    assert client.put("/api/v1/me/candidate-approvals/300829.SZ", json=payload(),
                      headers={"X-CSRF-Token": token}).status_code == 403


def test_real_app_origin_missing_csrf_and_login_required(client: TestClient) -> None:
    token = login(client, "approval-a")
    route = "/api/v1/me/candidate-approvals/300829.SZ"
    assert client.put(route, json=payload()).status_code == 403
    result = client.put(
        route, json=payload(),
        headers={"X-CSRF-Token": token, "Origin": "https://foreign.invalid"},
    )
    assert result.status_code == 403
    client.cookies.clear()
    assert client.get("/api/v1/me/candidate-approvals/state?snapshot_id=1").status_code == 401


def test_real_app_enabled_without_migration_degrades_feedback_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = make_app(tmp_path, monkeypatch, enabled=True)
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        login(client, "approval-a")
        assert client.get("/api/v1/state").status_code == 200
        assert client.get("/api/v1/me/candidate-approvals/state?snapshot_id=1").status_code == 503
    with database(app.state.settings.db_path) as connection:
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='web_candidate_approvals'"
        ).fetchone()


def test_real_app_default_off_preserves_original_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = make_app(tmp_path, monkeypatch, enabled=False)
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        login(client, "approval-a")
        html = client.get("/").text
        assert 'data-approvals-enabled="false"' in html
        assert '/static/candidate-approvals.css' not in html
        assert client.get("/api/v1/me/candidate-approvals/state?snapshot_id=1").status_code == 404
