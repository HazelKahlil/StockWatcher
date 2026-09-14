"""HTTP tests of the real router/repository with isolated auth dependencies.

The full project auth adapter is exercised separately in test_candidate_approval_app.py.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from approval_test_support import NOW, synthetic_database
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from stock_watcher.feedback.api import approval_router
from stock_watcher.feedback.repository import ApprovalRepository
from stock_watcher.feedback.schema import database


def build_test_app(path: Path, *, extension: bool = True) -> FastAPI:
    synthetic_database(path, extension=extension)
    app = FastAPI()

    def read(request: Request) -> dict[str, Any]:
        value = request.cookies.get("test-user")
        if value not in {"1", "2", "3"}:
            raise HTTPException(401)
        return {"user_id": int(value)}

    def write(request: Request, session: dict[str, Any] = Depends(read)) -> dict[str, Any]:
        if request.headers.get("Origin") != "http://testserver":
            raise HTTPException(403)
        if request.headers.get("X-CSRF-Token") != "isolated-test-only":
            raise HTTPException(403)
        return session

    def admin(session: dict[str, Any] = Depends(read)) -> dict[str, Any]:
        if session["user_id"] != 1:
            raise HTTPException(403)
        return session

    repository = ApprovalRepository(path, clock=lambda: NOW)
    app.include_router(approval_router(repository, read, write, admin))
    return app


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(build_test_app(tmp_path / "api.db")) as value:
        value.cookies.set("test-user", "2")
        value.headers.update({"Origin": "http://testserver", "X-CSRF-Token": "isolated-test-only"})
        yield value


def body(**changes: Any) -> dict[str, Any]:
    return {"snapshot_id": 1, "selected": True, "request_id": uuid.uuid4().hex,
            "expected_version": 0, **changes}


def test_http_save_and_private_read(client: TestClient) -> None:
    result = client.put("/api/v1/me/candidate-approvals/300829.SZ", json=body())
    assert result.status_code == 200
    assert "no-store" in result.headers["cache-control"]
    state = client.get("/api/v1/me/candidate-approvals/state?snapshot_id=1")
    assert state.json()["items"][0]["selected"] is True
    client.cookies.set("test-user", "3")
    state = client.get("/api/v1/me/candidate-approvals/state?snapshot_id=1").json()
    assert state["items"][0]["selected"] is False
    assert client.get("/api/v1/me/candidate-approvals/events").json()["items"] == []


@pytest.mark.parametrize("missing", ["Origin", "X-CSRF-Token"])
def test_write_requires_security_dependencies(client: TestClient, missing: str) -> None:
    del client.headers[missing]
    assert client.put("/api/v1/me/candidate-approvals/300829.SZ", json=body()).status_code == 403


def test_no_auth_cannot_read_or_write(client: TestClient) -> None:
    client.cookies.clear()
    assert client.get("/api/v1/me/candidate-approvals/state?snapshot_id=1").status_code == 401
    assert client.put("/api/v1/me/candidate-approvals/300829.SZ", json=body()).status_code == 401


@pytest.mark.parametrize(
    "extra",
    [{"user_id": 3}, {"price": 0.01}, {"selected": "yes"}, {"expected_version": True}],
)
def test_mass_assignment_and_non_strict_values_rejected(
    client: TestClient, extra: dict[str, Any],
) -> None:
    result = client.put("/api/v1/me/candidate-approvals/300829.SZ", json=body(**extra))
    assert result.status_code == 422


def test_admin_export_permissions(client: TestClient) -> None:
    client.put("/api/v1/me/candidate-approvals/300829.SZ", json=body())
    assert client.get("/api/v1/admin/candidate-approvals/events").status_code == 403
    client.cookies.set("test-user", "1")
    result = client.get("/api/v1/admin/candidate-approvals/events")
    assert result.status_code == 200
    assert len(result.json()["items"]) == 1


def test_invalid_snapshot_and_version_conflict(client: TestClient) -> None:
    path = "/api/v1/me/candidate-approvals/300829.SZ"
    assert client.put(path, json=body(snapshot_id=999)).status_code == 404
    assert client.put("/api/v1/me/candidate-approvals/300829.SZ", json=body()).status_code == 200
    assert client.put(path, json=body(selected=False)).status_code == 409


def test_missing_extension_is_explicit_not_fake_success(tmp_path: Path) -> None:
    path = tmp_path / "disabled.db"
    with TestClient(build_test_app(path, extension=False)) as value:
        value.cookies.set("test-user", "2")
        result = value.get("/api/v1/me/candidate-approvals/state?snapshot_id=1")
        assert result.status_code == 503
        assert result.json()["error"]["code"] == "feedback_unavailable"
    with database(path) as connection:
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='web_candidate_approvals'"
        ).fetchone()


def test_database_busy_is_not_reported_as_saved(tmp_path: Path) -> None:
    path = tmp_path / "busy.db"
    app = build_test_app(path)
    with database(path, write=True) as lock, TestClient(app) as value:
        lock.execute("BEGIN IMMEDIATE")
        value.cookies.set("test-user", "2")
        value.headers.update({"Origin": "http://testserver", "X-CSRF-Token": "isolated-test-only"})
        result = value.put("/api/v1/me/candidate-approvals/300829.SZ", json=body())
        assert result.status_code == 503
        lock.rollback()
        assert value.get("/api/v1/me/candidate-approvals/events").json()["items"] == []


def test_foreign_origin_rejected(client: TestClient) -> None:
    client.headers["Origin"] = "https://other.invalid"
    assert client.put("/api/v1/me/candidate-approvals/300829.SZ", json=body()).status_code == 403


def test_oversized_sqlite_identifiers_fail_as_validation_not_server_errors(
    client: TestClient,
) -> None:
    huge = 1 << 100
    route = "/api/v1/me/candidate-approvals"
    assert client.put(route + "/300829.SZ", json=body(snapshot_id=huge)).status_code == 422
    assert client.put(route + "/300829.SZ", json=body(expected_version=huge)).status_code == 422
    assert client.get(route + f"/state?snapshot_id={huge}").status_code == 422
    assert client.get(route + f"/events?cursor={huge}").status_code == 422
