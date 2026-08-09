"""Admin CLI safety tests for local backup, restore and credential handling."""
from __future__ import annotations

from pathlib import Path

import pytest

from stock_watcher.server.admin_cli import (
    _parse_preflight_scales,
    _replace_report_directory,
    parse_args,
)


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
