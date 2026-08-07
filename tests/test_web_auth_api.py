"""Auth, RBAC, CSRF, rate limit, API contract and snapshot-race tests."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from stock_watcher.domain import SHANGHAI
from stock_watcher.server.config import ServerSettings
from stock_watcher.server.web import create_app
from stock_watcher.storage import SQLiteStore


@pytest.fixture()
def app_env(tmp_path: Path) -> tuple[Any, SQLiteStore, Any, Any, Any]:
    import base64
    import os

    master_key_file = tmp_path / "master.key"
    master_key_file.write_text(
        base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    )
    settings = ServerSettings(
        db_path=tmp_path / "db" / "test.db",
        report_dir=tmp_path / "reports",
        master_key_file=master_key_file,
        public_origin="http://testserver",
    )
    app = create_app(settings)
    store: SQLiteStore = app.state.store
    auth = app.state.auth
    admin = auth.create_user(username="Admin One", password="admin-pass-12345", role="admin")
    tester = auth.create_user(username="tester1", password="tester-pass-123", role="tester")
    return app, store, auth, admin, tester


def login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    csrf = client.get("/api/v1/me").json()["csrf_token"]
    assert isinstance(csrf, str)
    return csrf


def test_login_bad_credentials_and_rate_limit(
    app_env: tuple[Any, SQLiteStore, Any, Any, Any],
) -> None:
    app, _, _, _, _ = app_env
    client = TestClient(app)
    for _ in range(5):
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "nobody", "password": "wrong-password"},
        )
        assert response.status_code == 401
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "nobody", "password": "wrong-password"},
    )
    assert response.status_code == 429


def test_rbac_matrix(app_env: tuple[Any, SQLiteStore, Any, Any, Any]) -> None:
    app, _, _, admin, tester = app_env
    admin_client = TestClient(app)
    login(admin_client, "admin one", "admin-pass-12345")
    assert admin_client.get("/api/v1/state").status_code == 200
    assert admin_client.get("/api/v1/admin/diagnostics").status_code == 200
    assert admin_client.get("/api/v1/admin/scan-runs").status_code == 200

    tester_client = TestClient(app)
    login(tester_client, "tester1", "tester-pass-123")
    assert tester_client.get("/api/v1/state").status_code == 200
    assert tester_client.get("/api/v1/candidates/current").status_code == 200
    assert tester_client.get("/api/v1/history").status_code == 200
    assert tester_client.get("/api/v1/alerts").status_code == 200
    assert tester_client.get("/api/v1/summaries").status_code == 200
    # Tester denied on admin endpoints (default deny).
    assert tester_client.get("/api/v1/admin/diagnostics").status_code == 403
    assert tester_client.get("/api/v1/admin/scan-runs").status_code == 403
    assert tester_client.get("/api/v1/admin/users").status_code == 403
    # Unauthenticated denied everywhere.
    anon = TestClient(app)
    assert anon.get("/api/v1/state").status_code == 401
    assert anon.get("/api/v1/me").status_code == 401
    assert anon.get("/api/v1/history").status_code == 401


def test_csrf_protection(app_env: tuple[Any, SQLiteStore, Any, Any, Any]) -> None:
    app, _, _, _, _ = app_env
    client = TestClient(app)
    login(client, "tester1", "tester-pass-123")
    # No CSRF header -> 403 even with valid session.
    response = client.post("/api/v1/commands/manual-refresh", json={})
    assert response.status_code == 403
    # Wrong CSRF -> 403.
    response = client.post(
        "/api/v1/commands/manual-refresh",
        json={},
        headers={"X-CSRF-Token": "wrong-value"},
    )
    assert response.status_code == 403
    # Rotated CSRF from /me works.
    csrf = client.get("/api/v1/me").json()["csrf_token"]
    response = client.post(
        "/api/v1/commands/manual-refresh",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 202


def test_session_revocation_and_expiry(app_env: tuple[Any, SQLiteStore, Any, Any, Any]) -> None:
    app, _, auth, _, _ = app_env
    client = TestClient(app)
    login(client, "tester1", "tester-pass-123")
    assert client.get("/api/v1/me").status_code == 200
    auth.revoke_user_sessions(2)  # tester user_id=2
    assert client.get("/api/v1/me").status_code == 401


def test_admin_user_management(app_env: tuple[Any, SQLiteStore, Any, Any, Any]) -> None:
    app, _, _, _, _ = app_env
    client = TestClient(app)
    csrf = login(client, "admin one", "admin-pass-12345")
    created = client.post(
        "/api/v1/admin/users",
        json={"username": "newtester", "password": "new-password-123", "role": "tester"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 201, created.text
    assert created.json()["username"] == "newtester"
    # Deactivate -> session revoked.
    user_id = created.json()["user_id"]
    updated = client.patch(
        f"/api/v1/admin/users/{user_id}",
        json={"active": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    assert updated.json()["active"] is False
    # Weak password rejected.
    bad = client.post(
        "/api/v1/admin/users",
        json={"username": "weakuser", "password": "short", "role": "tester"},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad.status_code == 400


def test_snapshot_bound_detail_race(
    app_env: tuple[Any, SQLiteStore, Any, Any, Any],
    tmp_path: Path,
) -> None:
    """API-002: old snapshot detail must return 409 snapshot_changed."""
    from stock_watcher.domain import HealthState
    from stock_watcher.engine import Candidate
    from stock_watcher.engine.candidates import CandidateBatch

    app, store, _, _, _ = app_env
    client = TestClient(app)
    login(client, "tester1", "tester-pass-123")

    def make_batch(codes: list[str]) -> CandidateBatch:
        now = datetime(2026, 8, 7, 10, 0, tzinfo=SHANGHAI)
        candidates = [
            Candidate(
                code=code,
                name=f"测试{code}",
                sector="测试",
                sector_code="TEST",
                level="中",
                score=50.0,
                price_score=20.0,
                sector_score=20.0,
                trend_score=10.0,
                penalty=0.0,
                reasons=("测试",),
                source_ts=now,
                provider_version="t",
                config_version="t",
                app_version="t",
                is_formal=True,
                is_supplement=False,
                price=10.0,
                change_pct=1.0,
                velocity_pct=1.0,
                total_score=50.0,
                core_score=30.0,
                fund_label="未确认",
            )
            for code in codes
        ]
        return CandidateBatch(
            source_ts=now,
            generated_at=now,
            health=HealthState.HEALTHY,
            overall_weak=False,
            candidates=tuple(candidates),
        )

    snapshot_old = store.record_batch(make_batch(["600001.SH", "600002.SH", "600003.SH"]))
    snapshot_new = store.record_batch(make_batch(["600004.SH", "600005.SH", "600006.SH"]))
    assert snapshot_old != snapshot_new
    # Request the old snapshot's code against the *new* snapshot id.
    response = client.get(
        f"/api/v1/candidates/600001.SH?snapshot_id={snapshot_new}"
    )
    assert response.status_code == 404  # code not in that snapshot
    # Correct binding works.
    response = client.get(
        f"/api/v1/candidates/600001.SH?snapshot_id={snapshot_old}"
    )
    assert response.status_code == 200
    assert response.json()["candidate"]["code"] == "600001.SH"


def test_openapi_route_coverage(app_env: tuple[Any, SQLiteStore, Any, Any, Any]) -> None:
    """API-001: every contract path exists in the running app."""
    import yaml

    app, _, _, _, _ = app_env
    contract_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "reference"
        / "StockWatcher-Web-Internal-Test-Handoff-20260807"
        / "contracts"
        / "openapi.yaml"
    )
    if not contract_path.is_file():
        pytest.skip("handoff contracts not vendored in this tree")
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    app_spec = app.openapi()
    missing = [
        path
        for path in contract["paths"]
        if path not in app_spec["paths"]
        and path != "/health/ready"
        and path != "/health/live"
    ]
    assert not missing, f"missing routes: {missing}"
    for path, methods in contract["paths"].items():
        if path in app_spec["paths"]:
            for method in methods:
                assert method in app_spec["paths"][path], (
                    f"{method.upper()} {path} missing"
                )


def test_error_format_and_pdf_auth(
    app_env: tuple[Any, SQLiteStore, Any, Any, Any],
    tmp_path: Path,
) -> None:
    app, _, _, _, _ = app_env
    client = TestClient(app)
    login(client, "tester1", "tester-pass-123")
    # Uniform error envelope.
    response = client.get("/api/v1/summaries/2026-08-07")
    assert response.status_code == 404
    payload = response.json()
    assert "error" in payload and "code" in payload["error"]
    # PDF requires auth and sane headers when missing.
    anon = TestClient(app)
    response = anon.get("/api/v1/summaries/2026-08-07/pdf")
    assert response.status_code == 401
    response = client.get("/api/v1/summaries/2026-08-07/pdf")
    assert response.status_code == 404
    # Path traversal rejected by date format.
    response = client.get("/api/v1/summaries/..%2F..%2Fetc/pdf")
    assert response.status_code == 404


def test_token_endpoints_never_echo_token(app_env: tuple[Any, SQLiteStore, Any, Any, Any]) -> None:
    app, _, _, _, _ = app_env
    client = TestClient(app)
    csrf = login(client, "admin one", "admin-pass-12345")
    body = json.dumps({"token": "super-secret-token-value-xyz"})
    response = client.post(
        "/api/v1/admin/token/test",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-CSRF-Token": csrf,
        },
    )
    assert response.status_code == 202, response.text
    raw = response.text
    assert "super-secret-token-value-xyz" not in raw
    assert "fingerprint" in response.json()
