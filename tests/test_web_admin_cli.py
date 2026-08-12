"""Admin CLI safety tests for local backup, restore and credential handling."""
from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from stock_watcher.server.admin_cli import (
    _parse_preflight_scales,
    _replace_report_directory,
    cmd_create_user,
    cmd_reset_password,
    parse_args,
)
from stock_watcher.server.auth import AuthError, AuthService
from stock_watcher.server.config import ServerSettings
from stock_watcher.storage import SQLiteStore


def test_restore_replaces_reports_without_leaving_stale_files(tmp_path: Path) -> None:
    source = tmp_path / "backup" / "reports"
    source.mkdir(parents=True)
    (source / "current.pdf").write_bytes(b"current")
    target = tmp_path / "runtime" / "reports"
    target.mkdir(parents=True)
    (target / "stale.pdf").write_bytes(b"stale")

    _replace_report_directory(source, target)

    assert (target / "current.pdf").read_bytes() == b"current"
    assert not (target / "stale.pdf").exists()


def test_restore_replaces_reports_inside_mounted_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "backup" / "reports"
    source.mkdir(parents=True)
    (source / "current.pdf").write_bytes(b"current")
    (source / "nested").mkdir()
    (source / "nested" / "summary.pdf").write_bytes(b"summary")
    target = tmp_path / "runtime" / "reports"
    target.mkdir(parents=True)
    (target / "stale.pdf").write_bytes(b"stale")
    (target / "stale-dir").mkdir()
    (target / "stale-dir" / "old.pdf").write_bytes(b"old")
    monkeypatch.setattr(Path, "is_mount", lambda path: path == target)

    _replace_report_directory(source, target)

    assert (target / "current.pdf").read_bytes() == b"current"
    assert (target / "nested" / "summary.pdf").read_bytes() == b"summary"
    assert not (target / "stale.pdf").exists()
    assert not (target / "stale-dir").exists()
    assert not (target / ".restore-tmp").exists()
    assert not (target / ".restore-old").exists()


def test_create_user_password_cannot_be_passed_on_argv() -> None:
    parser = parse_args()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "create-user",
                "--username",
                "admin",
                "--role",
                "admin",
                "--password",
                "secret-on-argv",
            ]
        )
    parsed = parser.parse_args(
        ["create-user", "--username", "admin", "--role", "admin", "--password-stdin"]
    )
    assert parsed.password_stdin is True


def test_reset_password_is_stdin_only_and_rotates_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = parse_args()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "reset-password",
                "--username",
                "admin",
                "--password",
                "secret-on-argv",
            ]
        )

    settings = ServerSettings(
        db_path=tmp_path / "db" / "test.db",
        report_dir=tmp_path / "reports",
    )
    monkeypatch.setattr("sys.stdin", StringIO("initial-password-123\n"))
    create_args = parser.parse_args(
        ["create-user", "--username", "admin", "--role", "admin", "--password-stdin"]
    )
    assert cmd_create_user(settings, create_args) == 0

    store = SQLiteStore(settings.db_path)
    auth = AuthService(store)
    session = auth.login(username="admin", password="initial-password-123")
    assert auth.sessions.get(str(session["token"])) is not None

    monkeypatch.setattr("sys.stdin", StringIO("replacement-password-456\n"))
    reset_args = parser.parse_args(
        ["reset-password", "--username", "admin", "--password-stdin"]
    )
    assert cmd_reset_password(settings, reset_args) == 0
    revoked = auth.sessions.get(str(session["token"]))
    assert revoked is not None
    assert revoked["revoked_at"] is not None
    assert auth.authenticate(str(session["token"])) is None
    with pytest.raises(AuthError):
        auth.login(username="admin", password="initial-password-123")
    assert auth.login(username="admin", password="replacement-password-456")


def test_preflight_scales_accept_full_without_parsing_it_as_integer() -> None:
    assert _parse_preflight_scales("1,100,300,800,full", full_count=5_321) == [
        1,
        100,
        300,
        800,
        5_321,
    ]
    with pytest.raises(ValueError):
        _parse_preflight_scales("0,full", full_count=5_321)
