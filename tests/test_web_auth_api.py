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
from stock_watcher.services import CommandType
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


def test_production_login_cookie_and_http_headers(tmp_path: Path) -> None:
    settings = ServerSettings(
        environment="production",
        db_path=tmp_path / "db" / "production.db",
        report_dir=tmp_path / "reports",
        public_origin="https://stock.example.com",
    )
    app = create_app(settings)
    app.state.auth.create_user(
        username="secure-admin",
        password="secure-admin-password",
        role="admin",
    )
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "secure-admin", "password": "secure-admin-password"},
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert client.get("/api/v1/openapi.json").status_code == 404


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
    assert tester_client.get("/api/v1/outcomes").status_code == 200
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
    assert anon.get("/api/v1/outcomes").status_code == 401


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
    # Stable per-session CSRF from /me works.
    csrf = client.get("/api/v1/me").json()["csrf_token"]
    response = client.post(
        "/api/v1/commands/manual-refresh",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 202


def test_csrf_remains_valid_across_multiple_tabs(
    app_env: tuple[Any, SQLiteStore, Any, Any, Any],
) -> None:
    app, _, _, _, _ = app_env
    client = TestClient(app)
    first = login(client, "tester1", "tester-pass-123")
    assert client.get("/alerts").status_code == 200
    second = client.get("/api/v1/me").json()["csrf_token"]
    assert first == second
    response = client.post(
        "/api/v1/commands/manual-refresh",
        headers={"X-CSRF-Token": first, "Idempotency-Key": "multi-tab"},
    )
    assert response.status_code == 202


def test_command_rate_limit_consumes_successful_requests(
    app_env: tuple[Any, SQLiteStore, Any, Any, Any],
) -> None:
    app, _, auth, _, _ = app_env
    auth.command_limiter.max_attempts = 2
    client = TestClient(app)
    csrf = login(client, "tester1", "tester-pass-123")
    for index in range(2):
        response = client.post(
            "/api/v1/commands/manual-refresh",
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"limit-{index}"},
        )
        assert response.status_code == 202
    blocked = client.post(
        "/api/v1/commands/manual-refresh",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "limit-blocked"},
    )
    assert blocked.status_code == 429


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


def test_password_change_revokes_all_existing_user_sessions(
    app_env: tuple[Any, SQLiteStore, Any, Any, Any],
) -> None:
    app, _, _, _, tester = app_env
    tester_client = TestClient(app)
    login(tester_client, "tester1", "tester-pass-123")
    admin_client = TestClient(app)
    csrf = login(admin_client, "admin one", "admin-pass-12345")
    changed = admin_client.patch(
        f"/api/v1/admin/users/{tester['user_id']}",
        json={"password": "tester-new-password-123"},
        headers={"X-CSRF-Token": csrf},
    )
    assert changed.status_code == 200
    assert tester_client.get("/api/v1/me").status_code == 401


def test_command_status_is_limited_to_requester_or_admin(
    app_env: tuple[Any, SQLiteStore, Any, Any, Any],
) -> None:
    app, _, auth, admin, tester = app_env
    private = app.state.commands.create(
        command_type=CommandType.TOKEN_TEST,
        requested_by=int(admin["user_id"]),
    )
    tester_client = TestClient(app)
    login(tester_client, "tester1", "tester-pass-123")
    assert tester_client.get(
        f"/api/v1/commands/{private['command_id']}"
    ).status_code == 404

    second = auth.create_user(
        username="tester2",
        password="tester-two-pass-123",
        role="tester",
    )
    shared = app.state.commands.create(
        command_type=CommandType.MANUAL_REFRESH,
        requested_by=int(tester["user_id"]),
    )
    second_client = TestClient(app)
    login(second_client, "tester2", "tester-two-pass-123")
    response = second_client.get(f"/api/v1/commands/{shared['command_id']}")
    assert response.status_code == 200
    assert response.json() == {
        "command_id": shared["command_id"],
        "command_type": "manual_refresh",
        "status": "queued",
        "coalesced": True,
    }
    assert second["user_id"] != tester["user_id"]


def test_last_active_admin_cannot_be_removed(
    app_env: tuple[Any, SQLiteStore, Any, Any, Any],
) -> None:
    app, _, _, admin, tester = app_env
    client = TestClient(app)
    csrf = login(client, "admin one", "admin-pass-12345")
    for payload in ({"role": "tester"}, {"active": False}):
        response = client.patch(
            f"/api/v1/admin/users/{admin['user_id']}",
            json=payload,
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "last_admin_required"
    invalid = client.patch(
        f"/api/v1/admin/users/{tester['user_id']}",
        json={"active": "false"},
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid.status_code == 400


def test_dashboard_assets_have_no_inline_styles() -> None:
    root = Path(__file__).resolve().parents[1]
    dashboard_template = (root / "src/stock_watcher/server/templates/dashboard.html").read_text(
        encoding="utf-8"
    )
    dashboard_script = (root / "src/stock_watcher/server/static/dashboard.js").read_text(
        encoding="utf-8"
    )
    assert 'id="strong-alerts"' in dashboard_template
    assert 'role="alertdialog"' in dashboard_script
    assert "showAutomaticAlert" in dashboard_script
    assert "'scheduled-09:45', 'scheduled-14:45'" in dashboard_script
    assert "setTimeout(() => toast.remove(), 220)" not in dashboard_script
    assert "15000" not in dashboard_script
    assert "alertRestoreTarget.focus()" in dashboard_script
    assert "keepAlertFocus(event)" in dashboard_script
    for relative in (
        "src/stock_watcher/server/templates/dashboard.html",
        "src/stock_watcher/server/static/dashboard.js",
    ):
        content = (root / relative).read_text(encoding="utf-8")
        assert 'style="' not in content


def test_morandi_theme_outcome_page_and_blue_notification_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    css = (root / "src/stock_watcher/server/static/app.css").read_text(encoding="utf-8")
    base = (root / "src/stock_watcher/server/templates/base.html").read_text(
        encoding="utf-8"
    )
    outcomes = (root / "src/stock_watcher/server/templates/outcomes.html").read_text(
        encoding="utf-8"
    )
    script = (root / "src/stock_watcher/server/static/outcomes.js").read_text(
        encoding="utf-8"
    )
    for token in (
        "--bg: #EDE8DF",
        "--card: #F7F3EC",
        "--fg: #32383A",
        "--line: #C9C2B7",
        "--mist-blue: #627F92",
        "--ashare-red: #A75F5A",
        "--ashare-green: #66806B",
        "--ashare-gold: #A68A58",
    ):
        assert token in css
    assert ".hero-action-button.btn-notify" in css
    assert "background: var(--mist-blue)" in css
    assert 'href="/outcomes"' in base
    assert 'data-outcome-range="week"' in outcomes
    assert 'data-outcome-range="month"' in outcomes
    assert 'data-outcome-range="all"' in outcomes
    assert "/api/v1/outcomes?range=" in script


def test_outcomes_api_is_authenticated_sanitized_and_range_limited(
    app_env: tuple[Any, SQLiteStore, Any, Any, Any],
) -> None:
    app, store, _, _, _ = app_env
    at = datetime(2026, 8, 11, 9, 45, tzinfo=SHANGHAI).isoformat()
    store.create_candidate_outcomes(
        [
            {
                "entry_snapshot_id": 1,
                "entry_alert_id": 1,
                "entry_trade_date": "2026-08-11",
                "slot": "09:45",
                "rank": 1,
                "code": "600001.SH",
                "name": "测试一号",
                "entry_price": 10.0,
                "entry_source_ts": at,
                "target_trade_date": "2026-08-12",
                "target_slot": "09:45",
                "quality": "UNAVAILABLE",
                "provider_version": "tushare-15000",
                "config_version": "test",
                "app_version": "0.6.0-alpha.4",
                "created_at": at,
                "updated_at": at,
                "status": "unavailable",
                "safe_reason": "historical_minute_missing_or_ambiguous",
                "next_retry_at": None,
            }
        ]
    )
    store.set_app_setting(
        "candidate_outcome_backfill_status",
        {"status": "partial", "settled": 18, "unavailable": 6, "pending": 3},
    )
    client = TestClient(app)
    login(client, "tester1", "tester-pass-123")

    response = client.get("/api/v1/outcomes?range=month")
    assert response.status_code == 200
    payload = response.json()
    assert payload["range"] == "month"
    assert payload["summary"]["total_count"] == 1
    assert payload["records"][0]["display_reason"] == "精确分钟行情不可验证，未纳入统计"
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "historical_minute_missing_or_ambiguous" not in serialized
    assert "已回补18笔" in payload["backfill"]["message"]
    assert client.get("/api/v1/outcomes?range=year").status_code == 400
    assert client.get("/outcomes").status_code == 200


def test_dashboard_refresh_retries_transient_command_poll_failures() -> None:
    root = Path(__file__).resolve().parents[1]
    dashboard_script = (root / "src/stock_watcher/server/static/dashboard.js").read_text(
        encoding="utf-8"
    )
    assert "const maxPollRetries = 4;" in dashboard_script
    assert "let pollInFlight = false;" in dashboard_script
    assert "连接暂时中断，正在重试" in dashboard_script
    assert "连接持续中断，正在确认刷新状态" in dashboard_script
    assert (
        "await loadState();\n"
        "              await finish('failed', '刷新连接中断，请稍后重试');"
    ) in dashboard_script
    assert "pollTimer = setInterval(poll, 2000)" not in dashboard_script


def test_login_uses_external_script_compatible_with_csp() -> None:
    root = Path(__file__).resolve().parents[1]
    login_template = (root / "src/stock_watcher/server/templates/login.html").read_text(
        encoding="utf-8"
    )
    login_script = (root / "src/stock_watcher/server/static/login.js").read_text(
        encoding="utf-8"
    )
    assert '<script type="module" src="/static/login.js?v=1"></script>' in login_template
    assert "fetch('/api/v1/auth/login'" in login_script
    assert "<script type=\"module\">" not in login_template


def test_base_template_declares_favicon_assets() -> None:
    root = Path(__file__).resolve().parents[1]
    base_template = (root / "src/stock_watcher/server/templates/base.html").read_text(
        encoding="utf-8"
    )
    static = root / "src/stock_watcher/server/static"
    assert 'href="/static/favicon.ico?v=1"' in base_template
    assert 'href="/static/favicon-32x32.png?v=1"' in base_template
    assert 'href="/static/apple-touch-icon.png?v=1"' in base_template
    assert (static / "favicon.ico").read_bytes().startswith(b"\x00\x00\x01\x00")
    for name in ("favicon-32x32.png", "apple-touch-icon.png", "stockwatcher-icon.png"):
        assert (static / name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


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
